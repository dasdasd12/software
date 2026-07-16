use serde::Serialize;
use std::{
    collections::VecDeque,
    io::{BufRead, BufReader},
    net::{SocketAddr, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex, MutexGuard},
    thread,
    time::{Duration, Instant},
};
use tauri::State;

const LOCAL_CORE_URL: &str = "ws://127.0.0.1:8765";
const LOCAL_CORE_ADDRESS: &str = "127.0.0.1:8765";
const START_TIMEOUT: Duration = Duration::from_secs(10);
const PROBE_INTERVAL: Duration = Duration::from_millis(100);
const PROBE_TIMEOUT: Duration = Duration::from_millis(200);
const STDERR_TAIL_LINES: usize = 32;
const STDERR_LINE_BYTES: usize = 8 * 1024;

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalCoreStatus {
    state: &'static str,
    managed: bool,
    pid: Option<u32>,
    url: &'static str,
    backend: Option<String>,
    last_error: Option<String>,
    stderr_tail: Vec<String>,
}

#[derive(Clone, Debug)]
struct LaunchSpec {
    backend: String,
    program: PathBuf,
    require_file: bool,
}

impl LaunchSpec {
    fn configured(backend: &str, program: PathBuf) -> Self {
        Self {
            backend: backend.to_owned(),
            program,
            require_file: false,
        }
    }

    fn local(backend: &str, program: PathBuf) -> Self {
        Self {
            backend: backend.to_owned(),
            program,
            require_file: true,
        }
    }

    fn on_path(backend: &str, program: &str) -> Self {
        Self {
            backend: backend.to_owned(),
            program: PathBuf::from(program),
            require_file: false,
        }
    }

    fn is_resolvable(&self) -> bool {
        !self.require_file || self.program.is_file()
    }
}

struct ManagedProcess {
    child: Child,
    backend: String,
    ready: bool,
}

struct LocalCoreInner {
    process: Mutex<Option<ManagedProcess>>,
    operation_gate: Mutex<()>,
    stderr_tail: Arc<Mutex<VecDeque<String>>>,
    last_error: Mutex<Option<String>>,
}

impl Drop for LocalCoreInner {
    fn drop(&mut self) {
        let process = self
            .process
            .get_mut()
            .unwrap_or_else(|error| error.into_inner());
        if let Some(mut process) = process.take() {
            let _ = process.child.kill();
            let _ = process.child.wait();
        }
    }
}

#[derive(Clone)]
pub struct LocalCoreManager {
    inner: Arc<LocalCoreInner>,
}

impl Default for LocalCoreManager {
    fn default() -> Self {
        Self {
            inner: Arc::new(LocalCoreInner {
                process: Mutex::new(None),
                operation_gate: Mutex::new(()),
                stderr_tail: Arc::new(Mutex::new(VecDeque::new())),
                last_error: Mutex::new(None),
            }),
        }
    }
}

impl LocalCoreManager {
    fn start(&self) -> LocalCoreStatus {
        let _operation_guard = lock(&self.inner.operation_gate);
        self.refresh_process();

        let mut managed_process_pending = false;
        {
            let mut process_slot = lock(&self.inner.process);
            if let Some(process) = process_slot.as_mut() {
                if process.ready || local_core_is_listening() {
                    process.ready = true;
                    *lock(&self.inner.last_error) = None;
                    return self.status_locked(&mut process_slot);
                }
                managed_process_pending = true;
            }
        }

        if managed_process_pending {
            if self.wait_until_ready().is_ok() {
                *lock(&self.inner.last_error) = None;
                return self.status();
            }
        }

        if local_core_is_listening() {
            *lock(&self.inner.last_error) = None;
            return self.status();
        }

        let source_root = match resolve_software_root() {
            Ok(path) => path,
            Err(error) => {
                *lock(&self.inner.last_error) = Some(error);
                return self.status();
            }
        };
        let server = source_root.join("src").join("bridge").join("server.py");
        let config = source_root.join("src").join("bridge").join("config.yaml");

        if !server.is_file() || !config.is_file() {
            *lock(&self.inner.last_error) = Some(format!(
                "Local Core source files were not found under {}.",
                source_root.display()
            ));
            return self.status();
        }

        lock(&self.inner.stderr_tail).clear();
        let mut failures = Vec::new();
        for spec in python_candidates(&source_root) {
            if !spec.is_resolvable() {
                failures.push(format!("{} was not found", spec.backend));
                continue;
            }

            match self.spawn(&spec, &source_root, &server, &config) {
                Ok(()) => match self.wait_until_ready() {
                    Ok(()) => {
                        *lock(&self.inner.last_error) = None;
                        return self.status();
                    }
                    Err(error) => failures.push(format!("{}: {error}", spec.backend)),
                },
                Err(error) => failures.push(format!("{}: {error}", spec.backend)),
            }
        }

        let error = if failures.is_empty() {
            "No Python launch candidates were available for Local Core.".to_owned()
        } else {
            format!("Local Core could not be started. {}", failures.join("; "))
        };
        *lock(&self.inner.last_error) = Some(error);
        self.status()
    }

    fn spawn(
        &self,
        spec: &LaunchSpec,
        source_root: &Path,
        server: &Path,
        config: &Path,
    ) -> Result<(), String> {
        let mut command = Command::new(&spec.program);
        command
            .arg("-u")
            .arg(server)
            .arg("--config")
            .arg(config)
            .arg("--workspace")
            .arg(source_root)
            .current_dir(source_root)
            .env("PYTHONIOENCODING", "utf-8")
            .env("PYTHONUNBUFFERED", "1")
            .env("PYTHONUTF8", "1")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::piped());
        configure_hidden_child(&mut command);

        let mut child = command.spawn().map_err(|error| error.to_string())?;
        if let Some(stderr) = child.stderr.take() {
            capture_stderr(stderr, self.inner.stderr_tail.clone());
        }

        *lock(&self.inner.process) = Some(ManagedProcess {
            child,
            backend: spec.backend.clone(),
            ready: false,
        });
        Ok(())
    }

    fn wait_until_ready(&self) -> Result<(), String> {
        let deadline = Instant::now() + START_TIMEOUT;
        loop {
            let exit_error = {
                let mut process_slot = lock(&self.inner.process);
                match process_slot.as_mut() {
                    Some(process) => match process.child.try_wait() {
                        Ok(None) => None,
                        Ok(Some(status)) => {
                            Some(format!("process exited before listening ({status})"))
                        }
                        Err(error) => Some(format!("process state could not be read: {error}")),
                    },
                    None => Some("managed process disappeared during startup".to_owned()),
                }
            };

            if let Some(error) = exit_error {
                self.reap_managed_process(false);
                return Err(error);
            }

            if local_core_is_listening() {
                let mut process_slot = lock(&self.inner.process);
                if let Some(process) = process_slot.as_mut() {
                    match process.child.try_wait() {
                        Ok(None) => {
                            process.ready = true;
                            return Ok(());
                        }
                        Ok(Some(status)) => {
                            drop(process_slot);
                            self.reap_managed_process(false);
                            return Err(format!(
                                "process exited while Local Core was becoming ready ({status})"
                            ));
                        }
                        Err(error) => {
                            drop(process_slot);
                            self.reap_managed_process(true);
                            return Err(format!("process state could not be read: {error}"));
                        }
                    }
                }
            }

            if Instant::now() >= deadline {
                self.reap_managed_process(true);
                return Err(format!(
                    "timed out after {} seconds waiting for {LOCAL_CORE_URL}",
                    START_TIMEOUT.as_secs()
                ));
            }
            thread::sleep(PROBE_INTERVAL);
        }
    }

    fn refresh_process(&self) {
        let reason = {
            let mut process_slot = lock(&self.inner.process);
            match process_slot.as_mut() {
                Some(process) => match process.child.try_wait() {
                    Ok(None) => None,
                    Ok(Some(status)) => Some(format!("Local Core exited with {status}.")),
                    Err(error) => Some(format!(
                        "Local Core process state could not be read: {error}"
                    )),
                },
                None => None,
            }
        };

        if let Some(reason) = reason {
            self.reap_managed_process(false);
            *lock(&self.inner.last_error) = Some(reason);
        }
    }

    fn reap_managed_process(&self, kill: bool) {
        if let Some(mut process) = lock(&self.inner.process).take() {
            if kill {
                let _ = process.child.kill();
            }
            let _ = process.child.wait();
        }
    }

    fn status(&self) -> LocalCoreStatus {
        self.refresh_process();
        let mut process_slot = lock(&self.inner.process);
        self.status_locked(&mut process_slot)
    }

    fn status_locked(&self, process_slot: &mut Option<ManagedProcess>) -> LocalCoreStatus {
        let (state, managed, pid, backend) = if let Some(process) = process_slot.as_mut() {
            if !process.ready && local_core_is_listening() {
                process.ready = true;
            }
            (
                if process.ready { "running" } else { "starting" },
                true,
                Some(process.child.id()),
                Some(process.backend.clone()),
            )
        } else if local_core_is_listening() {
            ("running", false, None, Some("external".to_owned()))
        } else if lock(&self.inner.last_error).is_some() {
            ("error", false, None, None)
        } else {
            ("stopped", false, None, None)
        };

        LocalCoreStatus {
            state,
            managed,
            pid,
            url: LOCAL_CORE_URL,
            backend,
            last_error: lock(&self.inner.last_error).clone(),
            stderr_tail: lock(&self.inner.stderr_tail).iter().cloned().collect(),
        }
    }

    pub fn shutdown(&self) {
        let _operation_guard = lock(&self.inner.operation_gate);
        self.reap_managed_process(true);
    }

    fn record_worker_error(&self, error: impl Into<String>) {
        *lock(&self.inner.last_error) = Some(error.into());
    }
}

fn resolve_software_root() -> Result<PathBuf, String> {
    for variable in ["KIIIE_LOCAL_CORE_ROOT", "KIIIE_CORE_ROOT"] {
        if let Some(path) = std::env::var_os(variable) {
            let path = absolute_path(PathBuf::from(path));
            if looks_like_software_root(&path) {
                return Ok(path);
            }
        }
    }

    let manifest_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .map(Path::to_path_buf);

    let mut candidates = Vec::new();
    if let Some(path) = manifest_root {
        candidates.push(path);
    }
    if let Ok(current_dir) = std::env::current_dir() {
        add_root_candidates(&mut candidates, &current_dir);
    }
    if let Ok(current_exe) = std::env::current_exe() {
        if let Some(directory) = current_exe.parent() {
            add_root_candidates(&mut candidates, directory);
        }
    }

    for candidate in candidates {
        let candidate = absolute_path(candidate);
        if looks_like_software_root(&candidate) {
            return Ok(candidate);
        }
    }

    Err(
        "The software workspace containing src/bridge/server.py could not be located. Set KIIIE_LOCAL_CORE_ROOT to the absolute software directory."
            .to_owned(),
    )
}

fn add_root_candidates(candidates: &mut Vec<PathBuf>, start: &Path) {
    for ancestor in start.ancestors() {
        candidates.push(ancestor.to_path_buf());
        candidates.push(ancestor.join("software"));
    }
}

fn absolute_path(path: PathBuf) -> PathBuf {
    let absolute = if path.is_absolute() {
        path
    } else {
        std::env::current_dir()
            .map(|current_dir| current_dir.join(&path))
            .unwrap_or(path)
    };
    absolute.canonicalize().unwrap_or(absolute)
}

fn looks_like_software_root(path: &Path) -> bool {
    path.join("src").join("bridge").join("server.py").is_file()
        && path
            .join("src")
            .join("bridge")
            .join("config.yaml")
            .is_file()
}

fn python_candidates(source_root: &Path) -> Vec<LaunchSpec> {
    let mut candidates = Vec::new();

    if let Some(path) = std::env::var_os("KIIIE_LOCAL_CORE_PYTHON") {
        candidates.push(LaunchSpec::configured(
            "KIIIE_LOCAL_CORE_PYTHON",
            PathBuf::from(path),
        ));
    }
    if let Some(path) = std::env::var_os("KIIIE_CORE_PYTHON") {
        candidates.push(LaunchSpec::configured(
            "KIIIE_CORE_PYTHON",
            PathBuf::from(path),
        ));
    }

    let scripts_directory = if cfg!(windows) { "Scripts" } else { "bin" };
    let local_python = if cfg!(windows) {
        "python.exe"
    } else {
        "python"
    };
    candidates.push(LaunchSpec::local(
        "software/.venv-claude",
        source_root
            .join(".venv-claude")
            .join(scripts_directory)
            .join(local_python),
    ));
    candidates.push(LaunchSpec::local(
        "software/.venv",
        source_root
            .join(".venv")
            .join(scripts_directory)
            .join(local_python),
    ));

    if cfg!(windows) {
        candidates.push(LaunchSpec::on_path("python on PATH", "python.exe"));
    } else {
        candidates.push(LaunchSpec::on_path("python3 on PATH", "python3"));
        candidates.push(LaunchSpec::on_path("python on PATH", "python"));
    }

    candidates
}

fn local_core_is_listening() -> bool {
    let address: SocketAddr = match LOCAL_CORE_ADDRESS.parse() {
        Ok(address) => address,
        Err(_) => return false,
    };
    TcpStream::connect_timeout(&address, PROBE_TIMEOUT).is_ok()
}

fn capture_stderr(stderr: impl std::io::Read + Send + 'static, tail: Arc<Mutex<VecDeque<String>>>) {
    thread::spawn(move || {
        let mut reader = BufReader::new(stderr);
        let mut bytes = Vec::new();
        loop {
            bytes.clear();
            match reader.read_until(b'\n', &mut bytes) {
                Ok(0) => break,
                Ok(_) => {
                    if bytes.len() > STDERR_LINE_BYTES {
                        bytes.truncate(STDERR_LINE_BYTES);
                    }
                    let line = String::from_utf8_lossy(&bytes)
                        .trim_end_matches(['\r', '\n'])
                        .to_owned();
                    if !line.is_empty() {
                        let mut tail = lock(&tail);
                        tail.push_back(line);
                        while tail.len() > STDERR_TAIL_LINES {
                            tail.pop_front();
                        }
                    }
                }
                Err(error) => {
                    let mut tail = lock(&tail);
                    tail.push_back(format!("Could not read Local Core stderr: {error}"));
                    while tail.len() > STDERR_TAIL_LINES {
                        tail.pop_front();
                    }
                    break;
                }
            }
        }
    });
}

#[cfg(windows)]
fn configure_hidden_child(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    command.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn configure_hidden_child(_command: &mut Command) {}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|error| error.into_inner())
}

#[tauri::command]
pub async fn local_core_start(
    state: State<'_, LocalCoreManager>,
) -> Result<LocalCoreStatus, String> {
    let manager = state.inner().clone();
    let fallback = manager.clone();
    match tauri::async_runtime::spawn_blocking(move || manager.start()).await {
        Ok(status) => Ok(status),
        Err(error) => {
            fallback.record_worker_error(format!("Local Core start worker failed: {error}"));
            Ok(fallback.status())
        }
    }
}

#[tauri::command]
pub fn local_core_status(state: State<'_, LocalCoreManager>) -> LocalCoreStatus {
    state.status()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn status_uses_the_frontend_contract() {
        let status = LocalCoreStatus {
            state: "running",
            managed: false,
            pid: None,
            url: LOCAL_CORE_URL,
            backend: Some("external".to_owned()),
            last_error: None,
            stderr_tail: vec![],
        };

        assert_eq!(
            serde_json::to_value(status).unwrap(),
            json!({
                "state": "running",
                "managed": false,
                "pid": null,
                "url": LOCAL_CORE_URL,
                "backend": "external",
                "lastError": null,
                "stderrTail": [],
            })
        );
    }

    #[test]
    fn manifest_layout_resolves_the_software_workspace() {
        let root = resolve_software_root().unwrap();
        assert!(root.join("src").join("bridge").join("server.py").is_file());
        assert!(root
            .join("src")
            .join("bridge")
            .join("config.yaml")
            .is_file());
    }
}

use serde::Serialize;
use serde_json::{json, Value};
use std::{
    collections::{HashMap, VecDeque},
    ffi::OsString,
    io::{self, BufRead, BufReader, Write},
    path::PathBuf,
    process::{Child, ChildStdin, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Sender},
        Arc, Mutex, MutexGuard,
    },
    thread,
    time::Duration,
};
#[cfg(not(debug_assertions))]
use tauri::Manager;
use tauri::{AppHandle, Emitter, State};

const MAX_REQUEST_BYTES: usize = 32 * 1024 * 1024;
const MAX_RESPONSE_BYTES: usize = 32 * 1024 * 1024;
const STDERR_TAIL_LINES: usize = 24;
const STDERR_LINE_BYTES: usize = 8 * 1024;

const ALLOWED_METHODS: &[&str] = &[
    "bridge.hello",
    "device.list_ports",
    "device.connect",
    "device.info",
    "device.disconnect",
    "profile.factory.get",
    "profile.compile",
    "profile.install",
    "device.activate",
];

type PendingReply = Result<Value, BridgeCommandError>;

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BridgeCommandError {
    code: String,
    message: String,
    recoverable: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    details: Option<Value>,
}

impl BridgeCommandError {
    fn new(code: &str, message: impl Into<String>, recoverable: bool) -> Self {
        Self {
            code: code.to_owned(),
            message: message.into(),
            recoverable,
            details: None,
        }
    }

    fn with_details(mut self, details: Value) -> Self {
        self.details = Some(details);
        self
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BridgeStatus {
    state: &'static str,
    mode: Option<String>,
    backend: Option<String>,
    pid: Option<u32>,
    last_error: Option<String>,
    stderr_tail: Vec<String>,
    allowed_methods: &'static [&'static str],
}

#[derive(Clone)]
struct LaunchSpec {
    label: String,
    mode: String,
    program: PathBuf,
    args: Vec<OsString>,
    current_dir: Option<PathBuf>,
    env: Vec<(OsString, OsString)>,
}

impl LaunchSpec {
    fn sidecar(label: impl Into<String>, program: PathBuf) -> Self {
        let current_dir = program.parent().map(PathBuf::from);
        Self {
            label: label.into(),
            mode: "sidecar".to_owned(),
            program,
            args: vec![OsString::from("--stdio")],
            current_dir,
            env: vec![],
        }
    }

    #[cfg(debug_assertions)]
    fn python(label: impl Into<String>, program: PathBuf, source_root: &std::path::Path) -> Self {
        let source_dir = source_root.join("src");
        let mut python_paths = vec![source_dir.clone()];
        if let Some(existing) = std::env::var_os("PYTHONPATH") {
            python_paths.extend(std::env::split_paths(&existing));
        }
        let python_path = std::env::join_paths(python_paths).unwrap_or_else(|_| source_dir.into());

        Self {
            label: label.into(),
            mode: "development-python".to_owned(),
            program,
            args: vec![
                OsString::from("-u"),
                OsString::from("-m"),
                OsString::from("desktop_bridge"),
                OsString::from("--stdio"),
                OsString::from("--factory-profile"),
                source_root
                    .join("config")
                    .join("factory_default_profile.json")
                    .into_os_string(),
            ],
            current_dir: Some(source_root.to_path_buf()),
            env: vec![
                (OsString::from("PYTHONPATH"), python_path),
                (OsString::from("PYTHONIOENCODING"), OsString::from("utf-8")),
                (OsString::from("PYTHONUNBUFFERED"), OsString::from("1")),
            ],
        }
    }

    fn is_resolvable(&self) -> bool {
        !self.program.is_absolute() || self.program.is_file()
    }
}

struct BridgeProcess {
    child: Child,
    stdin: Arc<Mutex<ChildStdin>>,
    healthy: Arc<AtomicBool>,
    label: String,
    mode: String,
}

struct BridgeInner {
    process: Mutex<Option<BridgeProcess>>,
    pending: Arc<Mutex<HashMap<String, Sender<PendingReply>>>>,
    request_gate: Mutex<()>,
    stderr_tail: Arc<Mutex<VecDeque<String>>>,
    last_error: Arc<Mutex<Option<String>>>,
    last_launch: Mutex<Option<(String, String)>>,
}

impl Drop for BridgeInner {
    fn drop(&mut self) {
        let process = self
            .process
            .get_mut()
            .unwrap_or_else(|error| error.into_inner());
        if let Some(mut process) = process.take() {
            process.healthy.store(false, Ordering::Release);
            let _ = process.child.kill();
            let _ = process.child.wait();
        }
    }
}

#[derive(Clone)]
pub struct BridgeManager {
    inner: Arc<BridgeInner>,
}

impl Default for BridgeManager {
    fn default() -> Self {
        Self {
            inner: Arc::new(BridgeInner {
                process: Mutex::new(None),
                pending: Arc::new(Mutex::new(HashMap::new())),
                request_gate: Mutex::new(()),
                stderr_tail: Arc::new(Mutex::new(VecDeque::new())),
                last_error: Arc::new(Mutex::new(None)),
                last_launch: Mutex::new(None),
            }),
        }
    }
}

impl BridgeManager {
    fn request(&self, app: &AppHandle, request: Value) -> PendingReply {
        let _request_guard = lock(&self.inner.request_gate);
        let (request_id, method) = validate_request(&request)?;
        let encoded = serde_json::to_vec(&request).map_err(|error| {
            BridgeCommandError::new(
                "bridge_request_encode_failed",
                "The request could not be encoded for the local bridge.",
                false,
            )
            .with_details(json!({ "cause": error.to_string() }))
        })?;

        if encoded.len() > MAX_REQUEST_BYTES {
            return Err(BridgeCommandError::new(
                "bridge_request_too_large",
                format!(
                    "The request is {} bytes; the local bridge limit is {} bytes.",
                    encoded.len(),
                    MAX_REQUEST_BYTES
                ),
                false,
            ));
        }

        let stdin = self.ensure_started(app)?;
        let (sender, receiver) = mpsc::channel();
        {
            let mut pending = lock(&self.inner.pending);
            if pending.contains_key(&request_id) {
                return Err(BridgeCommandError::new(
                    "bridge_duplicate_request_id",
                    "A bridge request with this id is already pending.",
                    false,
                ));
            }
            pending.insert(request_id.clone(), sender);
        }

        let write_result = {
            let mut stdin = lock(&stdin);
            stdin
                .write_all(&encoded)
                .and_then(|_| stdin.write_all(b"\n"))
                .and_then(|_| stdin.flush())
        };

        if let Err(error) = write_result {
            lock(&self.inner.pending).remove(&request_id);
            self.stop_process("The local bridge input pipe closed unexpectedly.");
            return Err(BridgeCommandError::new(
                "bridge_write_failed",
                "The request could not be sent to the local bridge.",
                true,
            )
            .with_details(json!({ "cause": error.to_string() })));
        }

        match receiver.recv_timeout(timeout_for_method(&method)) {
            Ok(reply) => reply,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                lock(&self.inner.pending).remove(&request_id);
                self.stop_process("The local bridge did not respond before its safety timeout.");
                Err(BridgeCommandError::new(
                    "bridge_request_timeout",
                    format!("The local bridge timed out while handling {method}."),
                    true,
                ))
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => Err(BridgeCommandError::new(
                "bridge_disconnected",
                "The local bridge stopped before returning a response.",
                true,
            )),
        }
    }

    fn ensure_started(
        &self,
        app: &AppHandle,
    ) -> Result<Arc<Mutex<ChildStdin>>, BridgeCommandError> {
        let mut process_slot = lock(&self.inner.process);
        let mut stale_reason = None;

        if let Some(process) = process_slot.as_mut() {
            match process.child.try_wait() {
                Ok(None) if process.healthy.load(Ordering::Acquire) => {
                    return Ok(process.stdin.clone());
                }
                Ok(Some(status)) => {
                    stale_reason = Some(format!("The local bridge exited with {status}."));
                }
                Ok(None) => {
                    stale_reason = Some("The local bridge protocol reader stopped.".to_owned());
                }
                Err(error) => {
                    stale_reason =
                        Some(format!("The local bridge state could not be read: {error}"));
                }
            }
        }

        if let Some(reason) = stale_reason {
            if let Some(mut process) = process_slot.take() {
                process.healthy.store(false, Ordering::Release);
                let _ = process.child.kill();
                let _ = process.child.wait();
            }
            *lock(&self.inner.last_error) = Some(reason);
        }

        let candidates = launch_candidates(app);
        let mut failures = Vec::new();

        for spec in candidates {
            if !spec.is_resolvable() {
                failures.push(format!("{} was not found", spec.label));
                continue;
            }

            match spawn_process(app, &spec, &self.inner) {
                Ok(process) => {
                    let stdin = process.stdin.clone();
                    *lock(&self.inner.last_launch) =
                        Some((process.label.clone(), process.mode.clone()));
                    *lock(&self.inner.last_error) = None;
                    *process_slot = Some(process);
                    return Ok(stdin);
                }
                Err(error) => failures.push(format!("{}: {error}", spec.label)),
            }
        }

        let detail = if failures.is_empty() {
            "No bridge launch candidates were configured.".to_owned()
        } else {
            failures.join("; ")
        };
        *lock(&self.inner.last_error) = Some(detail.clone());

        Err(BridgeCommandError::new(
            "bridge_backend_unavailable",
            "The keyboard service is unavailable. In development, set KIIIE_CORE_PYTHON to a Python 3.11 executable; packaged builds require kiiie-core beside the app.",
            true,
        )
        .with_details(json!({ "attempts": detail })))
    }

    fn stop_process(&self, reason: &str) {
        *lock(&self.inner.last_error) = Some(reason.to_owned());
        if let Some(mut process) = lock(&self.inner.process).take() {
            process.healthy.store(false, Ordering::Release);
            let _ = process.child.kill();
            let _ = process.child.wait();
        }
    }

    fn status(&self, app: &AppHandle) -> BridgeStatus {
        let mut state = "stopped";
        let mut mode = None;
        let mut backend = None;
        let mut pid = None;
        let mut exited_reason = None;

        {
            let mut process_slot = lock(&self.inner.process);
            if let Some(process) = process_slot.as_mut() {
                match process.child.try_wait() {
                    Ok(None) if process.healthy.load(Ordering::Acquire) => {
                        state = "running";
                        mode = Some(process.mode.clone());
                        backend = Some(process.label.clone());
                        pid = Some(process.child.id());
                    }
                    Ok(Some(status)) => {
                        exited_reason = Some(format!("The local bridge exited with {status}."));
                    }
                    Ok(None) => {
                        exited_reason =
                            Some("The local bridge protocol reader stopped.".to_owned());
                    }
                    Err(error) => {
                        exited_reason =
                            Some(format!("The local bridge state could not be read: {error}"));
                    }
                }
            }

            if let Some(reason) = exited_reason.as_ref() {
                if let Some(mut process) = process_slot.take() {
                    process.healthy.store(false, Ordering::Release);
                    let _ = process.child.kill();
                    let _ = process.child.wait();
                }
                *lock(&self.inner.last_error) = Some(reason.clone());
            }
        }

        if state != "running" {
            if let Some((last_backend, last_mode)) = lock(&self.inner.last_launch).clone() {
                backend = Some(last_backend);
                mode = Some(last_mode);
            } else {
                let candidates = launch_candidates(app);
                if let Some(candidate) = candidates
                    .iter()
                    .find(|candidate| candidate.is_resolvable())
                {
                    backend = Some(candidate.label.clone());
                    mode = Some(candidate.mode.clone());
                } else {
                    state = "unavailable";
                }
            }
        }

        BridgeStatus {
            state,
            mode,
            backend,
            pid,
            last_error: lock(&self.inner.last_error).clone(),
            stderr_tail: lock(&self.inner.stderr_tail).iter().cloned().collect(),
            allowed_methods: ALLOWED_METHODS,
        }
    }
}

fn spawn_process(
    app: &AppHandle,
    spec: &LaunchSpec,
    inner: &BridgeInner,
) -> io::Result<BridgeProcess> {
    let mut command = Command::new(&spec.program);
    command
        .args(&spec.args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    if let Some(current_dir) = spec.current_dir.as_ref() {
        command.current_dir(current_dir);
    }
    for (key, value) in &spec.env {
        command.env(key, value);
    }
    configure_hidden_child(&mut command);

    let mut child = command.spawn()?;
    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| io::Error::other("bridge stdin was not piped"))?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| io::Error::other("bridge stdout was not piped"))?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| io::Error::other("bridge stderr was not piped"))?;

    let healthy = Arc::new(AtomicBool::new(true));
    start_stdout_reader(
        stdout,
        inner.pending.clone(),
        healthy.clone(),
        inner.last_error.clone(),
        app.clone(),
    );
    start_stderr_reader(stderr, inner.stderr_tail.clone());

    Ok(BridgeProcess {
        child,
        stdin: Arc::new(Mutex::new(stdin)),
        healthy,
        label: spec.label.clone(),
        mode: spec.mode.clone(),
    })
}

fn start_stdout_reader(
    stdout: std::process::ChildStdout,
    pending: Arc<Mutex<HashMap<String, Sender<PendingReply>>>>,
    healthy: Arc<AtomicBool>,
    last_error: Arc<Mutex<Option<String>>>,
    app: AppHandle,
) {
    thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let failure = loop {
            let line = match read_bounded_line(&mut reader, MAX_RESPONSE_BYTES) {
                Ok(Some(line)) => line,
                Ok(None) => break "The local bridge closed its output stream.".to_owned(),
                Err(error) => break format!("The local bridge protocol failed: {error}"),
            };

            if line.iter().all(u8::is_ascii_whitespace) {
                continue;
            }

            let message: Value = match serde_json::from_slice(trim_line_ending(&line)) {
                Ok(message) => message,
                Err(error) => {
                    break format!("The local bridge returned invalid JSON: {error}");
                }
            };

            if message.get("event").and_then(Value::as_str).is_some() {
                let _ = app.emit("bridge:event", &message);
                continue;
            }

            let Some(request_id) = message.get("id").and_then(Value::as_str) else {
                break "The local bridge returned a response without an id.".to_owned();
            };
            if message.get("ok").and_then(Value::as_bool).is_none() {
                break format!(
                    "The local bridge response for {request_id} has no boolean ok field."
                );
            }

            if let Some(sender) = lock(&pending).remove(request_id) {
                let _ = sender.send(Ok(message));
            } else {
                let _ = app.emit("bridge:orphan-response", json!({ "id": request_id }));
            }
        };

        healthy.store(false, Ordering::Release);
        *lock(&last_error) = Some(failure.clone());
        let error = BridgeCommandError::new("bridge_disconnected", failure.clone(), true);
        for (_, sender) in lock(&pending).drain() {
            let _ = sender.send(Err(error.clone()));
        }
        let _ = app.emit(
            "bridge:status",
            json!({ "state": "disconnected", "message": failure }),
        );
    });
}

fn start_stderr_reader(
    stderr: std::process::ChildStderr,
    stderr_tail: Arc<Mutex<VecDeque<String>>>,
) {
    thread::spawn(move || {
        let mut reader = BufReader::new(stderr);
        loop {
            let line = match read_bounded_line(&mut reader, STDERR_LINE_BYTES) {
                Ok(Some(line)) => line,
                Ok(None) => return,
                Err(error) => {
                    push_stderr_tail(&stderr_tail, format!("[stderr reader] {error}"));
                    return;
                }
            };
            let line = String::from_utf8_lossy(trim_line_ending(&line)).to_string();
            if !line.trim().is_empty() {
                push_stderr_tail(&stderr_tail, line);
            }
        }
    });
}

fn push_stderr_tail(stderr_tail: &Mutex<VecDeque<String>>, line: String) {
    let mut tail = lock(stderr_tail);
    while tail.len() >= STDERR_TAIL_LINES {
        tail.pop_front();
    }
    tail.push_back(line);
}

fn read_bounded_line<R: BufRead>(reader: &mut R, max_bytes: usize) -> io::Result<Option<Vec<u8>>> {
    let mut output = Vec::new();
    loop {
        let available = reader.fill_buf()?;
        if available.is_empty() {
            return if output.is_empty() {
                Ok(None)
            } else {
                Ok(Some(output))
            };
        }

        let newline = available.iter().position(|byte| *byte == b'\n');
        let take = newline.map_or(available.len(), |index| index + 1);
        if output.len().saturating_add(take) > max_bytes {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("a JSONL record exceeded {max_bytes} bytes"),
            ));
        }
        output.extend_from_slice(&available[..take]);
        reader.consume(take);

        if newline.is_some() {
            return Ok(Some(output));
        }
    }
}

fn trim_line_ending(mut line: &[u8]) -> &[u8] {
    if line.ends_with(b"\n") {
        line = &line[..line.len() - 1];
    }
    if line.ends_with(b"\r") {
        line = &line[..line.len() - 1];
    }
    line
}

fn validate_request(request: &Value) -> Result<(String, String), BridgeCommandError> {
    let object = request.as_object().ok_or_else(|| {
        BridgeCommandError::new(
            "bridge_invalid_request",
            "A bridge request must be a JSON object.",
            false,
        )
    })?;

    if let Some(unknown) = object
        .keys()
        .find(|key| !matches!(key.as_str(), "id" | "method" | "params"))
    {
        return Err(BridgeCommandError::new(
            "bridge_invalid_request",
            format!("Unknown bridge request field: {unknown}."),
            false,
        ));
    }

    let request_id = object.get("id").and_then(Value::as_str).ok_or_else(|| {
        BridgeCommandError::new(
            "bridge_invalid_request_id",
            "A bridge request requires a string id.",
            false,
        )
    })?;
    if request_id.is_empty()
        || request_id.len() > 128
        || !request_id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"-_.:".contains(&byte))
    {
        return Err(BridgeCommandError::new(
            "bridge_invalid_request_id",
            "The request id must be 1-128 ASCII letters, digits, dashes, underscores, dots, or colons.",
            false,
        ));
    }

    let method = object
        .get("method")
        .and_then(Value::as_str)
        .ok_or_else(|| {
            BridgeCommandError::new(
                "bridge_invalid_method",
                "A bridge request requires a string method.",
                false,
            )
        })?;
    if !ALLOWED_METHODS.contains(&method) {
        return Err(BridgeCommandError::new(
            "bridge_method_not_allowed",
            format!("The bridge method {method} is not exposed to the UI."),
            false,
        ));
    }

    if let Some(params) = object.get("params") {
        if !params.is_object() && !params.is_null() {
            return Err(BridgeCommandError::new(
                "bridge_invalid_params",
                "Bridge request params must be an object or null.",
                false,
            ));
        }
    }

    Ok((request_id.to_owned(), method.to_owned()))
}

fn timeout_for_method(method: &str) -> Duration {
    match method {
        "profile.install" => Duration::from_secs(300),
        "profile.compile" => Duration::from_secs(60),
        "device.connect" => Duration::from_secs(30),
        _ => Duration::from_secs(20),
    }
}

fn launch_candidates(app: &AppHandle) -> Vec<LaunchSpec> {
    let mut candidates = Vec::new();

    if let Some(path) = std::env::var_os("KIIIE_CORE_SIDECAR") {
        candidates.push(LaunchSpec::sidecar(
            "KIIIE_CORE_SIDECAR",
            PathBuf::from(path),
        ));
    }

    #[cfg(debug_assertions)]
    {
        let source_root = std::env::var_os("KIIIE_CORE_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .parent()
                    .and_then(|path| path.parent())
                    .expect("src-tauri must be nested under software/desktop")
                    .to_path_buf()
            });

        if let Some(path) = std::env::var_os("KIIIE_CORE_PYTHON") {
            candidates.push(LaunchSpec::python(
                "KIIIE_CORE_PYTHON",
                PathBuf::from(path),
                &source_root,
            ));
        }

        let scripts_dir = if cfg!(windows) { "Scripts" } else { "bin" };
        let executable = if cfg!(windows) {
            "python.exe"
        } else {
            "python"
        };
        candidates.push(LaunchSpec::python(
            "software/.venv-claude",
            source_root
                .join(".venv-claude")
                .join(scripts_dir)
                .join(executable),
            &source_root,
        ));
        candidates.push(LaunchSpec::python(
            "software/.venv",
            source_root.join(".venv").join(scripts_dir).join(executable),
            &source_root,
        ));
        candidates.push(LaunchSpec::python(
            "python on PATH",
            PathBuf::from(executable),
            &source_root,
        ));
    }

    #[cfg(not(debug_assertions))]
    {
        let executable = if cfg!(windows) {
            "kiiie-core.exe"
        } else {
            "kiiie-core"
        };
        if let Ok(resource_dir) = app.path().resource_dir() {
            candidates.push(LaunchSpec::sidecar(
                "bundled kiiie-core",
                resource_dir.join(executable),
            ));
            candidates.push(LaunchSpec::sidecar(
                "bundled binaries/kiiie-core",
                resource_dir.join("binaries").join(executable),
            ));
        }
        if let Ok(current_exe) = std::env::current_exe() {
            if let Some(directory) = current_exe.parent() {
                candidates.push(LaunchSpec::sidecar(
                    "adjacent kiiie-core",
                    directory.join(executable),
                ));
            }
        }
    }

    let _ = app;
    candidates
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
pub async fn bridge_request(
    app: AppHandle,
    state: State<'_, BridgeManager>,
    request: Value,
) -> Result<Value, BridgeCommandError> {
    let manager = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || manager.request(&app, request))
        .await
        .map_err(|error| {
            BridgeCommandError::new(
                "bridge_task_failed",
                "The desktop bridge worker could not complete the request.",
                true,
            )
            .with_details(json!({ "cause": error.to_string() }))
        })?
}

#[tauri::command]
pub fn bridge_status(app: AppHandle, state: State<'_, BridgeManager>) -> BridgeStatus {
    state.status(&app)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validates_the_public_envelope() {
        let request = json!({
            "id": "req-1",
            "method": "profile.factory.get",
            "params": {}
        });

        assert_eq!(
            validate_request(&request).unwrap(),
            ("req-1".to_owned(), "profile.factory.get".to_owned())
        );
    }

    #[test]
    fn rejects_methods_outside_the_ui_allowlist() {
        let request = json!({
            "id": "req-2",
            "method": "system.exec",
            "params": { "command": "whoami" }
        });

        let error = validate_request(&request).unwrap_err();
        assert_eq!(error.code, "bridge_method_not_allowed");
    }

    #[test]
    fn reads_a_partial_final_jsonl_record() {
        let mut reader = BufReader::new(&b"{\"ok\":true}"[..]);
        assert_eq!(
            read_bounded_line(&mut reader, 64).unwrap(),
            Some(b"{\"ok\":true}".to_vec())
        );
        assert_eq!(read_bounded_line(&mut reader, 64).unwrap(), None);
    }
}

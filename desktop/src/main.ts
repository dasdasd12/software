import { createPinia } from "pinia";
import { createApp } from "vue";
import App from "./App.vue";
import router from "./router";
import "./styles/tokens.css";
import "./styles/shell.css";
import "./styles/components.css";
import "./styles/keyboard.css";
import "./styles/pages.css";
import "./styles/app.css";

createApp(App).use(createPinia()).use(router).mount("#app");

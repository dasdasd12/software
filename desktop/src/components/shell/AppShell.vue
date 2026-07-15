<script setup lang="ts">
import { RouterView } from "vue-router";
import SideNavigation from "./SideNavigation.vue";
import TopBar from "./TopBar.vue";
</script>

<template>
  <main class="app-shell">
    <SideNavigation />
    <section class="workspace">
      <TopBar />
      <div class="page-root">
        <RouterView />
      </div>
    </section>
  </main>
</template>

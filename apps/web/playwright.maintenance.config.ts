import { defineConfig } from '@playwright/test';
export default defineConfig({testDir:'tests',testMatch:'maintenance.spec.ts',workers:1,use:{baseURL:'http://127.0.0.1:46607',serviceWorkers:'block'},reporter:'list'});

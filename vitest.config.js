// Vitest harness for browser-side JS modules (cache.js, auth.js).
// Tests live in tests/js/. JSDOM provides window/document/storage.
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['tests/js/**/*.test.js'],
    globals: false,
  },
});

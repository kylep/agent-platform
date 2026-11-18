import {defineConfig} from '@playwright/test';

export default defineConfig({
  testDir: './design-capture',
  testMatch: 'capture.spec.ts',
  outputDir: '../../.ap/site-design-capture/test-results',
  fullyParallel: true,
  workers: 4,
  timeout: 45_000,
  retries: 0,
  use: {
    baseURL: 'http://127.0.0.1:6009', viewport: {width:1440,height:1000},
    deviceScaleFactor: 1, locale:'en-US', timezoneId:'UTC', contextOptions:{reducedMotion:'reduce'},
    screenshot:'only-on-failure',
  },
  webServer: {command:'node design-capture/server.mjs', url:'http://127.0.0.1:6009', reuseExistingServer:false},
});

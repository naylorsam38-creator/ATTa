# apps/mobile — React Native 0.88 client

Status screen against `/v1/health/ready` using the shared `@atta/api_client` and `@atta/design_system`.
It has its own `package-lock.json` (Metro does not support the root workspace's hoisting).

    cd apps/mobile && npm ci && npm start        # Metro
    npm run android | npm run ios                # needs Android SDK / Xcode (BLOCKERS.md B-005)

Native `android/` and `ios/` projects are generated with the React Native CLI template for 0.88.1 when the
mobile toolchain is available; they are not part of Stage 01 (no SDKs in the build environment).

const path = require("path");
const { getDefaultConfig, mergeConfig } = require("@react-native/metro-config");

const root = path.resolve(__dirname, "../..");
module.exports = mergeConfig(getDefaultConfig(__dirname), {
  watchFolders: [path.join(root, "packages")],
  resolver: { nodeModulesPaths: [path.join(__dirname, "node_modules")] },
});

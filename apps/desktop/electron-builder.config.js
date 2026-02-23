const path = require("node:path");

function parseOwnerRepo() {
  const fromEnv = String(process.env.GITHUB_REPOSITORY || "").trim();
  if (fromEnv.includes("/")) {
    const [owner, repo] = fromEnv.split("/");
    if (owner && repo) return { owner, repo };
  }

  const owner = String(process.env.DESKTOP_PUBLISH_OWNER || "yanhaishui").trim();
  const repo = String(process.env.DESKTOP_PUBLISH_REPO || "LiveTalking").trim();
  return { owner, repo };
}

function resolvePublish() {
  const provider = String(process.env.DESKTOP_PUBLISH_PROVIDER || "github").trim().toLowerCase();
  if (provider === "none" || provider === "off" || provider === "disable") return undefined;

  if (provider === "github") {
    const { owner, repo } = parseOwnerRepo();
    if (!owner || !repo) return undefined;
    return [
      {
        provider: "github",
        owner,
        repo,
        releaseType: String(process.env.DESKTOP_PUBLISH_RELEASE_TYPE || "release").trim() || "release",
      },
    ];
  }

  if (provider === "generic") {
    const url = String(process.env.DESKTOP_PUBLISH_URL || "").trim();
    if (!url) return undefined;
    return [
      {
        provider: "generic",
        url,
      },
    ];
  }

  return undefined;
}

const publish = resolvePublish();

module.exports = {
  appId: "com.meh.livetalking.desktop",
  productName: "MEH Digital Human",
  directories: {
    output: "dist",
  },
  files: ["main.js", "preload.js", "renderer/**/*", "scripts/**/*", "!renderer/**/*.map"],
  extraResources: [
    {
      from: path.join(__dirname, "..", "web_admin"),
      to: "web_admin",
      filter: ["**/*"],
    },
    {
      from: path.join(__dirname, "..", "..", "assets", "main.png"),
      to: path.join("assets", "main.png"),
    },
  ],
  asar: true,
  artifactName: "${productName}-${version}-${os}-${arch}.${ext}",
  afterSign: path.join("scripts", "notarize.js"),
  generateUpdatesFilesForAllChannels: true,
  mac: {
    target: ["dmg"],
    category: "public.app-category.productivity",
    hardenedRuntime: true,
    gatekeeperAssess: false,
  },
  win: {
    target: [
      {
        target: "nsis",
        arch: ["x64"],
      },
    ],
  },
  nsis: {
    oneClick: false,
    allowToChangeInstallationDirectory: true,
  },
  publish,
};

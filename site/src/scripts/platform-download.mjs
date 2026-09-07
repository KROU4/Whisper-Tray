const WINDOWS = "windows";

/**
 * Resolve the three supported desktop platforms. Phones, tablets, ChromeOS,
 * and unknown systems intentionally receive the Windows download.
 */
export function detectPlatform(navigatorLike = globalThis.navigator) {
  const userAgent = navigatorLike?.userAgent?.toLowerCase() ?? "";
  if (userAgent.includes("android") || userAgent.includes("cros")) return WINDOWS;

  const platform = (
    navigatorLike?.userAgentData?.platform
    || navigatorLike?.platform
    || userAgent
  ).toLowerCase();

  if (platform.includes("mac")) return "macos";
  if (platform.includes("linux")) return "linux";
  return WINDOWS;
}

export function configurePlatformDownloads(root = document, navigatorLike = navigator) {
  const platform = detectPlatform(navigatorLike);
  root.querySelectorAll(".platform-download").forEach((link) => {
    const url = link.dataset[`download${capitalize(platform)}`];
    const label = link.dataset[`label${capitalize(platform)}`];
    if (url) link.href = url;
    link.querySelectorAll("[data-download-icon]").forEach((icon) => {
      icon.hidden = icon.dataset.downloadIcon !== platform;
    });
    const labelNode = link.querySelector("[data-download-label]");
    if (labelNode && label) labelNode.textContent = label;
  });
}

function capitalize(value) {
  return value[0].toUpperCase() + value.slice(1);
}

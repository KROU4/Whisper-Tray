import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";
import icon from "astro-icon";

export default defineConfig({
  site: "https://krou4.github.io",
  base: "/Whisper-Tray",
  output: "static",
  devToolbar: { enabled: false },
  integrations: [
    sitemap(),
    icon({
      iconDir: "src",
      include: {
        mdi: [
          "microsoft-windows",
          "github",
          "shield-lock-outline",
          "lightning-bolt-outline",
          "monitor-cellphone",
          "keyboard-outline",
          "translate",
          "download",
          "plus"
        ],
      },
    }),
  ],
  trailingSlash: "always",
  vite: {
    server: {
      host: "0.0.0.0",
    },
  },
});

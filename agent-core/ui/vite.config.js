import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * Deployed, `/api/*` is a second CloudFront behaviour pointing at the proxy's
 * Lambda function URL, so the page and the API share an origin and there is no
 * CORS anywhere.
 *
 * `npm run dev` has no CloudFront, so the proxy below reproduces that same-origin
 * arrangement against `scripts/ui_server.py` — which runs the supervisor in this
 * process against Ollama and the four local servers. Point it somewhere else with
 * API_ORIGIN=https://<distribution> to develop the UI against the deployed stack.
 */
export default defineConfig({
    plugins: [react()],
    server: {
        port: 5173,
        proxy: {
            "/api": {
                target: process.env.API_ORIGIN || "http://127.0.0.1:8123",
                changeOrigin: true,
            },
        },
    },
    build: {
        // Hashed filenames, so `Cache-Control: max-age=31536000` on the assets is
        // safe and only index.html needs to be revalidated. 08_ui sets exactly
        // that split when it uploads.
        outDir: "dist",
        emptyOutDir: true,
    },
});

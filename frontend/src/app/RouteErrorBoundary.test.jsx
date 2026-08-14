/* The boundary must tell chunk-load failures apart from ordinary runtime errors (only the
   former may trigger a reload), and must extract the attempted chunk path for logging. */
import { describe, it, expect } from "vitest";
import { isChunkLoadError, chunkPathFromError } from "./RouteErrorBoundary.jsx";

describe("isChunkLoadError", () => {
  it("matches the chunk-load signatures", () => {
    expect(isChunkLoadError(Object.assign(new Error("x"), { name: "ChunkLoadError" }))).toBe(true);
    expect(isChunkLoadError(new Error("Failed to fetch dynamically imported module: https://x/AppRoot-abc.js"))).toBe(true);
    expect(isChunkLoadError(new Error("Importing a module script failed."))).toBe(true);
    expect(isChunkLoadError(new Error("Loading chunk 5 failed"))).toBe(true);
  });

  it("does NOT match ordinary runtime errors (a reload would just reproduce them)", () => {
    expect(isChunkLoadError(new TypeError("Cannot read properties of undefined (reading 'map')"))).toBe(false);
    expect(isChunkLoadError(new Error("Network request failed"))).toBe(false);
    expect(isChunkLoadError(null)).toBe(false);
  });

  it("extracts the attempted chunk URL for logging", () => {
    const url = "https://app.example.com/assets/AppRoot-9f2a.js";
    expect(chunkPathFromError(new Error(`Failed to fetch dynamically imported module: ${url}`))).toBe(url);
    expect(chunkPathFromError(new Error("no url here"))).toBeNull();
  });
});

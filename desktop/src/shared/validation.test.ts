import { describe, expect, it } from "vitest";
import {
  parseAddSource,
  parseCredentialCreate,
  parseOperationSearch,
  requiredString,
} from "./validation";

describe("IPC input validation", () => {
  it("normalizes a source request", () => {
    expect(parseAddSource({ kind: "url", value: " https://example.com/openapi.json " })).toEqual({
      kind: "url",
      value: "https://example.com/openapi.json",
    });
  });

  it("rejects unsupported source kinds", () => {
    expect(() => parseAddSource({ kind: "command", value: "rm -rf" })).toThrow(
      "Unsupported source kind",
    );
  });

  it("does not allow an empty credential", () => {
    expect(() => parseCredentialCreate({ label: "token", value: "  " })).toThrow(
      "Credential value is required",
    );
  });

  it("caps operation search inputs", () => {
    expect(() => parseOperationSearch({ query: "test", limit: 500 })).toThrow(
      "Limit must be between 1 and 50",
    );
  });

  it("rejects non-string identifiers", () => {
    expect(() => requiredString({ id: "x" }, "id")).toThrow("id is required");
  });
});

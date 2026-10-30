import { describe, it, expect } from "vitest";
import { quickFor } from "./session";
describe("session", () => {
  it("puts quick tasks for the signed-in profession first", () => {
    expect(quickFor("Surgeon")[0]?.role).toBe("Surgeon");
  });
});

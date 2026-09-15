import type { TFunction } from "i18next"
import { describe, expect, it } from "vitest"
import { windowName } from "./quota-window"

describe("Antigravity quota window labels", () => {
  const t = ((key: string) => key) as TFunction
  it.each(["gemini-5h", "gemini-weekly", "3p-5h", "3p-weekly"])("localizes %s separately", (key) => {
    expect(windowName(key, t)).toBe(`usage.windowNames.${key}`)
  })
  it("does not guess the window of legacy per-model observations", () => {
    expect(windowName("gemini-pro-agent", t)).toBe("gemini-pro-agent")
  })
})

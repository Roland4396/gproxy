import type { TFunction } from "i18next"

export function quotaWindowName(key: string, t: TFunction, label?: string | null): string {
  const antigravity: Record<string, string> = {
    "gemini-5h": "limits.geminiFiveHourQuota", "gemini-weekly": "limits.geminiWeeklyQuota",
    "3p-5h": "limits.thirdPartyFiveHourQuota", "3p-weekly": "limits.thirdPartyWeeklyQuota",
  }
  if (antigravity[key]) {
    const name = t(antigravity[key])
    return label === "antigravity_disabled" ? t("limits.inactiveQuota", { window: name }) : name
  }
  if (key === "five_hour") return t("limits.fiveHourQuota")
  if (key === "seven_day") return t("limits.sevenDayQuota")
  if (key.startsWith("weekly_model:") || key.startsWith("weekly_surface:")) return t("limits.scopedWeeklyQuota", { scope: label ?? key.split(":")[1].replaceAll("_", " ") })
  if (key.startsWith("seven_day_")) return t("limits.scopedWeeklyQuota", { scope: key.slice("seven_day_".length).replaceAll("_", " ") })
  return key
}

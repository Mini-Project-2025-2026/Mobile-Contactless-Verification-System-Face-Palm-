/**
 * Design tokens matched to the KNUST Attendance visual language
 * (light theme, crimson-red diagonal gradient, white rounded cards).
 * Derived from the App Store screenshots.
 */
export const colors = {
  bg: "#F5F6F8",
  card: "#FFFFFF",
  text: "#12151C",
  textMuted: "#6B7280",
  textFaint: "#9AA1AC",
  primary: "#C41230",
  primaryDark: "#6E0D1A",
  primaryTint: "#FCE9EC", // light-red icon chip / row background
  border: "#ECEEF1",
  success: "#16A34A",
  successBg: "#E7F8EF",
  warning: "#D97706",
  warningBg: "#FEF3E2",
  danger: "#DC2626",
  onGradient: "#FFFFFF",
  onGradientDim: "rgba(255,255,255,0.72)",
  overlayChip: "rgba(255,255,255,0.16)",
};

/** Diagonal crimson gradient used on hero cards (top-left -> bottom-right). */
export const gradient = {
  colors: [colors.primary, colors.primaryDark] as const,
  start: { x: 0, y: 0 },
  end: { x: 1, y: 1 },
};

export const radius = { sm: 10, md: 14, lg: 20, xl: 26, pill: 999 };
export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 };

export const shadow = {
  card: {
    shadowColor: "#0B1220",
    shadowOpacity: 0.06,
    shadowRadius: 14,
    shadowOffset: { width: 0, height: 6 },
    elevation: 3,
  },
};

export const font = {
  h1: { fontSize: 26, fontWeight: "800" as const, color: colors.text },
  h2: { fontSize: 20, fontWeight: "800" as const, color: colors.text },
  title: { fontSize: 17, fontWeight: "700" as const, color: colors.text },
  body: { fontSize: 15, fontWeight: "500" as const, color: colors.text },
  muted: { fontSize: 13, fontWeight: "500" as const, color: colors.textMuted },
  label: { fontSize: 12, fontWeight: "600" as const, color: colors.textFaint },
};

export const statusColor: Record<string, { fg: string; bg: string }> = {
  present: { fg: colors.success, bg: colors.successBg },
  partial: { fg: colors.warning, bg: colors.warningBg },
  absent: { fg: colors.danger, bg: "#FDECEC" },
};

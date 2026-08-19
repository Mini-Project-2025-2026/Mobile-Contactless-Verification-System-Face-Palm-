/** Small shared presentational pieces: Avatar, StatusPill, ListRow, IconDot. */
import React from "react";
import { Pressable, StyleSheet, Text, View, ViewStyle } from "react-native";
import { colors, radius, shadow, space, statusColor } from "../theme";

export function Avatar({ initials, size = 52 }: { initials: string; size?: number }) {
  return (
    <View style={[styles.avatar, { width: size, height: size, borderRadius: size / 2 }]}>
      <Text style={[styles.avatarText, { fontSize: size * 0.38 }]}>{initials}</Text>
      <View style={styles.presence} />
    </View>
  );
}

export function StatusPill({ status }: { status: "present" | "partial" | "absent" }) {
  const c = statusColor[status];
  return (
    <View style={[styles.pill, { backgroundColor: c.bg }]}>
      <Text style={[styles.pillText, { color: c.fg }]}>{status[0].toUpperCase() + status.slice(1)}</Text>
    </View>
  );
}

/** A tinted rounded square holding an emoji/glyph — the left icon on list rows. */
export function IconDot({ glyph }: { glyph: string }) {
  return (
    <View style={styles.iconDot}>
      <Text style={styles.iconGlyph}>{glyph}</Text>
    </View>
  );
}

export function ListRow({
  glyph,
  title,
  subtitle,
  right,
  onPress,
  style,
}: {
  glyph: string;
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
  onPress?: () => void;
  style?: ViewStyle;
}) {
  return (
    <Pressable style={[styles.row, style]} onPress={onPress} disabled={!onPress}>
      <IconDot glyph={glyph} />
      <View style={{ flex: 1 }}>
        <Text style={styles.rowTitle}>{title}</Text>
        {subtitle ? <Text style={styles.rowSub}>{subtitle}</Text> : null}
      </View>
      {right ?? <Text style={styles.chevron}>›</Text>}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  avatar: { backgroundColor: "#4C6FFF", alignItems: "center", justifyContent: "center" },
  avatarText: { color: "#fff", fontWeight: "800" },
  presence: { position: "absolute", right: 2, bottom: 2, width: 12, height: 12, borderRadius: 6, backgroundColor: colors.success, borderWidth: 2, borderColor: "#fff" },

  pill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: radius.pill },
  pillText: { fontSize: 12, fontWeight: "700" },

  iconDot: { width: 38, height: 38, borderRadius: 12, backgroundColor: colors.primaryTint, alignItems: "center", justifyContent: "center" },
  iconGlyph: { fontSize: 18 },

  row: { flexDirection: "row", alignItems: "center", gap: space.md, backgroundColor: colors.card, borderRadius: radius.md, padding: space.md, ...shadow.card },
  rowTitle: { fontSize: 15, fontWeight: "700", color: colors.text },
  rowSub: { fontSize: 12.5, color: colors.textMuted, marginTop: 2 },
  chevron: { fontSize: 22, color: colors.textFaint, fontWeight: "600" },
});

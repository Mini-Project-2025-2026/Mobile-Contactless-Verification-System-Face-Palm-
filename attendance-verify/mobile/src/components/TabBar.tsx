import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { colors, shadow } from "../theme";

export type TabKey = "home" | "discover" | "devices" | "profile";

const TABS: { key: TabKey; label: string; glyph: string }[] = [
  { key: "home", label: "Home", glyph: "⌂" },
  { key: "discover", label: "Discover", glyph: "◎" },
  { key: "devices", label: "Devices", glyph: "▢" },
  { key: "profile", label: "Profile", glyph: "◍" },
];

export default function TabBar({ active, onChange }: { active: TabKey; onChange: (k: TabKey) => void }) {
  return (
    <SafeAreaView edges={["bottom"]} style={styles.safe}>
      <View style={styles.bar}>
        {TABS.map((t) => {
          const on = t.key === active;
          return (
            <Pressable key={t.key} style={styles.tab} onPress={() => onChange(t.key)} hitSlop={8}>
              <Text style={[styles.glyph, on && styles.glyphOn]}>{t.glyph}</Text>
              <Text style={[styles.label, on && styles.labelOn]}>{t.label}</Text>
            </Pressable>
          );
        })}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { backgroundColor: colors.card },
  bar: { flexDirection: "row", backgroundColor: colors.card, paddingTop: 10, paddingBottom: 6, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border, ...shadow.card },
  tab: { flex: 1, alignItems: "center", gap: 3 },
  glyph: { fontSize: 20, color: colors.textFaint },
  glyphOn: { color: colors.primary },
  label: { fontSize: 11, fontWeight: "600", color: colors.textFaint },
  labelOn: { color: colors.primary },
});

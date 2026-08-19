import React, { useState } from "react";
import { View, Text, StyleSheet, Pressable } from "react-native";
import Screen from "../components/Screen";
import GradientCard from "../components/GradientCard";
import { colors, radius, shadow, space, colors as C } from "../theme";

const SECTIONS: { title: string; items: { q: string; a: string }[] }[] = [
  {
    title: "Getting Started",
    items: [
      { q: "How do I sign in to the app?", a: "Use your student ID and password. This phone is registered as your device on first sign-in." },
      { q: "What devices are supported?", a: "One active device per student. Manage it from the Devices tab." },
      { q: "How do I enable location permissions?", a: "Allow location when prompted — it's used only to confirm you're inside the class geofence." },
    ],
  },
  {
    title: "Marking Attendance",
    items: [
      { q: "How do I mark my attendance?", a: "Open Home or Discover when a class is live and in range, tap Verify with Face, and complete the head-turn check." },
      { q: "Why a face check instead of a code?", a: "A live biometric check can't be shared or entered for an absent friend, so attendance is genuinely yours." },
      { q: "Why did my attendance fail to mark?", a: "Common reasons: outside the geofence, poor lighting, or the session has closed. Move closer, improve lighting, and retry." },
    ],
  },
];

export default function HelpScreen({ onBack }: { onBack: () => void }) {
  const [open, setOpen] = useState<string | null>(null);

  return (
    <Screen>
      <View style={styles.header}>
        <Pressable onPress={onBack} hitSlop={10}><Text style={styles.back}>‹</Text></Pressable>
        <View>
          <Text style={styles.title}>Help Center</Text>
          <Text style={styles.sub}>Everything you need to know</Text>
        </View>
      </View>

      <GradientCard>
        <Text style={styles.heroTitle}>Attendance Verify</Text>
        <Text style={styles.heroSub}>Your guide to seamless, tamper-proof attendance.</Text>
        <View style={styles.heroBox}>
          <Text style={styles.heroBody}>
            Mark attendance quickly and securely using location + a live face check.
            Browse the topics below or contact support if you need more help.
          </Text>
        </View>
      </GradientCard>

      {SECTIONS.map((sec) => (
        <View key={sec.title} style={{ gap: space.sm }}>
          <Text style={styles.section}>{sec.title}</Text>
          {sec.items.map((it) => {
            const isOpen = open === it.q;
            return (
              <Pressable key={it.q} style={styles.item} onPress={() => setOpen(isOpen ? null : it.q)}>
                <View style={styles.itemRow}>
                  <Text style={styles.q}>{it.q}</Text>
                  <Text style={styles.chev}>{isOpen ? "⌄" : "›"}</Text>
                </View>
                {isOpen ? <Text style={styles.a}>{it.a}</Text> : null}
              </Pressable>
            );
          })}
        </View>
      ))}
    </Screen>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", gap: space.md },
  back: { fontSize: 30, color: colors.text, fontWeight: "700" },
  title: { fontSize: 20, fontWeight: "800", color: colors.text },
  sub: { fontSize: 13, color: colors.textMuted },
  heroTitle: { color: C.onGradient, fontSize: 18, fontWeight: "800" },
  heroSub: { color: C.onGradientDim, fontSize: 13, marginTop: 2 },
  heroBox: { backgroundColor: C.overlayChip, borderRadius: radius.sm, padding: 12, marginTop: 12 },
  heroBody: { color: C.onGradient, fontSize: 13, lineHeight: 19 },
  section: { fontSize: 15, fontWeight: "800", color: colors.text, marginTop: space.sm },
  item: { backgroundColor: colors.card, borderRadius: radius.md, padding: space.md, ...shadow.card },
  itemRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  q: { fontSize: 14.5, fontWeight: "700", color: colors.text, flex: 1 },
  chev: { fontSize: 20, color: colors.textFaint },
  a: { fontSize: 13.5, color: colors.textMuted, marginTop: 8, lineHeight: 19 },
});

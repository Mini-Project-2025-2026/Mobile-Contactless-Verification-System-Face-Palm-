import React, { useEffect, useState } from "react";
import { View, Text, StyleSheet, Pressable } from "react-native";
import Screen from "../components/Screen";
import GradientCard from "../components/GradientCard";
import { Avatar, ListRow } from "../components/ui";
import { api, Profile } from "../api/client";
import { colors, radius, space, colors as C } from "../theme";

export default function ProfileScreen({
  name,
  onOpenHistory,
  onOpenHelp,
  onEnroll,
  onSignOut,
}: {
  name: string;
  onOpenHistory: () => void;
  onOpenHelp: () => void;
  onEnroll: () => void;
  onSignOut: () => void;
}) {
  const [p, setP] = useState<Profile | null>(null);

  useEffect(() => { api.profile().then(setP).catch(() => {}); }, []);

  const displayName = p?.name ?? name;
  const initials = displayName.split(" ").map((s) => s[0]).slice(0, 2).join("").toUpperCase() || "ST";

  return (
    <Screen>
      <View style={styles.headerCard}>
        <Text style={styles.headerTitle}>Profile</Text>
        <Text style={styles.bell}>🔔</Text>
      </View>

      <GradientCard>
        <View style={styles.idTop}>
          <Avatar initials={initials} size={56} />
          <View style={{ flex: 1 }}>
            <Text style={styles.idName}>{displayName}</Text>
            <Text style={styles.idRef}>Reference: {p?.reference_no || "—"}</Text>
          </View>
        </View>
        <View style={styles.chip}>
          <Text style={styles.chipLabel}>Programme</Text>
          <Text style={styles.chipValue}>{p?.programme || "—"}</Text>
        </View>
        <View style={styles.chipRow}>
          <View style={[styles.chip, styles.chipHalf]}>
            <Text style={styles.chipLabel}>Year Group</Text>
            <Text style={styles.chipValue}>{p?.year_group || "—"}</Text>
          </View>
          <View style={[styles.chip, styles.chipHalf]}>
            <Text style={styles.chipLabel}>Class Group</Text>
            <Text style={styles.chipValue}>{p?.class_group || "—"}</Text>
          </View>
        </View>
      </GradientCard>

      <Text style={styles.section}>Options</Text>
      <ListRow
        glyph="🖐"
        title="Biometric Enrolment"
        subtitle={p?.enrolled ? "Enrolled" : "Not enrolled — tap to set up"}
        onPress={onEnroll}
      />
      <ListRow glyph="🗓" title="My Attendance" onPress={onOpenHistory} />
      <ListRow glyph="🔒" title="Change Password" onPress={() => {}} />
      <ListRow glyph="❔" title="Help Center" onPress={onOpenHelp} />

      <Pressable style={styles.logout} onPress={onSignOut}>
        <Text style={styles.logoutText}>⤶  Logout</Text>
      </Pressable>
    </Screen>
  );
}

const styles = StyleSheet.create({
  headerCard: { backgroundColor: colors.card, borderRadius: radius.md, padding: space.lg, flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  headerTitle: { fontSize: 18, fontWeight: "800", color: colors.text },
  bell: { fontSize: 18 },

  idTop: { flexDirection: "row", alignItems: "center", gap: space.md, marginBottom: space.md },
  idName: { color: C.onGradient, fontSize: 19, fontWeight: "800" },
  idRef: { color: C.onGradientDim, fontSize: 13, marginTop: 2 },
  chip: { backgroundColor: C.overlayChip, borderRadius: radius.sm, paddingHorizontal: 12, paddingVertical: 10, marginTop: 8 },
  chipRow: { flexDirection: "row", gap: 8 },
  chipHalf: { flex: 1 },
  chipLabel: { color: C.onGradientDim, fontSize: 12 },
  chipValue: { color: C.onGradient, fontSize: 15, fontWeight: "700", marginTop: 2 },

  section: { fontSize: 15, fontWeight: "800", color: colors.text, marginTop: space.sm },
  logout: { backgroundColor: colors.primaryTint, borderRadius: radius.md, padding: 16, alignItems: "center", marginTop: space.sm },
  logoutText: { color: colors.primary, fontWeight: "800", fontSize: 15 },
});

import React, { useCallback, useEffect, useState } from "react";
import { View, Text, StyleSheet, Pressable, ActivityIndicator, ScrollView, Image } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import * as Location from "expo-location";
import { api, AvailableCourse } from "../api/client";
import { colors, radius, shadow } from "../theme";

type Gps = { lat: number; lng: number; accuracy_m?: number };

export default function DiscoverScreen({ onCheckIn }: { onCheckIn: (c: AvailableCourse, gps: Gps) => void }) {
  const [gps, setGps] = useState<Gps | null>(null);
  const [sessions, setSessions] = useState<AvailableCourse[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== "granted") return;
      const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
      const g = { lat: pos.coords.latitude, lng: pos.coords.longitude, accuracy_m: pos.coords.accuracy ?? undefined };
      setGps(g);
      setSessions(await api.available(g.lat, g.lng));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const anyInRange = sessions.some((s) => s.in_range);

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <Image source={require('../../assets/map_bg.jpg')} style={styles.mapBg} />
      <View style={styles.header}>
        <View style={[styles.pill, anyInRange ? styles.pillIn : styles.pillOut]}>
          <Text style={styles.pillText}>{anyInRange ? "Inside Class Area" : "Outside Class Area"}</Text>
        </View>
        <Text style={styles.sessionCount}>◍ {sessions.length} Active Session{sessions.length === 1 ? "" : "s"}</Text>
      </View>

      {loading && !gps ? (
        <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
      ) : !gps ? (
        <View style={styles.center}><Text style={styles.msg}>Enable location to see nearby classes.</Text></View>
      ) : (
        <ScrollView contentContainerStyle={styles.list}>
          {sessions.length === 0 ? (
            <Text style={styles.emptyText}>No active sessions nearby.</Text>
          ) : (
            sessions.map((s) => (
              <Pressable 
                key={s.session_id} 
                style={[styles.sessionCard, !s.in_range && styles.sessionCardOut]}
                onPress={() => {
                  if (s.in_range && gps) onCheckIn(s, gps);
                }}
              >
                <Text style={styles.sessionCode}>{s.course_code}</Text>
                <Text style={styles.sessionTitle}>{s.title || "Class Session"}</Text>
                <Text style={styles.sessionStatus}>
                  {s.in_range ? "📍 Tap to Check In" : "❌ Out of range"}
                </Text>
              </Pressable>
            ))
          )}
        </ScrollView>
      )}

      <View style={styles.fabs}>
        <Pressable style={styles.fab} onPress={load}><Text style={styles.fabGlyph}>↻</Text></Pressable>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
  mapBg: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, width: "100%", height: "100%", opacity: 0.25, resizeMode: "cover" },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  msg: { color: colors.textMuted },
  header: { padding: 16, alignItems: "center", gap: 10 },
  pill: { paddingHorizontal: 20, paddingVertical: 12, borderRadius: radius.md, ...shadow.card, width: "100%", alignItems: "center" },
  pillIn: { backgroundColor: colors.success },
  pillOut: { backgroundColor: colors.primary },
  pillText: { color: "#fff", fontWeight: "800", fontSize: 16 },
  sessionCount: { color: colors.text, fontWeight: "600", fontSize: 13, backgroundColor: colors.card, paddingHorizontal: 12, paddingVertical: 6, borderRadius: radius.pill, overflow: "hidden" },
  list: { padding: 16, gap: 12 },
  emptyText: { textAlign: "center", color: colors.textMuted, marginTop: 40 },
  sessionCard: { backgroundColor: colors.card, padding: 16, borderRadius: radius.lg, ...shadow.sm, borderWidth: 1, borderColor: colors.border },
  sessionCardOut: { opacity: 0.6 },
  sessionCode: { fontSize: 18, fontWeight: "800", color: colors.primary, marginBottom: 4 },
  sessionTitle: { fontSize: 15, color: colors.text, marginBottom: 12 },
  sessionStatus: { fontSize: 14, fontWeight: "600", color: colors.textMuted },
  fabs: { position: "absolute", right: 16, bottom: 40, gap: 12 },
  fab: { width: 50, height: 50, borderRadius: 25, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center", ...shadow.card },
  fabGlyph: { color: "#fff", fontSize: 22, fontWeight: "700" },
});

import React, { useCallback, useEffect, useState } from "react";
import { View, Text, StyleSheet, Pressable, ActivityIndicator, Alert } from "react-native";
import * as Location from "expo-location";
import Screen from "../components/Screen";
import GradientCard from "../components/GradientCard";
import { Avatar } from "../components/ui";
import { api, AvailableCourse } from "../api/client";
import { colors, radius, shadow, space, colors as C } from "../theme";

type Gps = { lat: number; lng: number; accuracy_m?: number };

export default function HomeScreen({
  name,
  onCheckIn,
  enrolled,
  onEnroll,
}: {
  name: string;
  onCheckIn: (c: AvailableCourse, gps: Gps) => void;
  enrolled: boolean;
  onEnroll: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const [gps, setGps] = useState<Gps | null>(null);
  const [courses, setCourses] = useState<AvailableCourse[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== "granted") return;
      const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
      const g = { lat: pos.coords.latitude, lng: pos.coords.longitude, accuracy_m: pos.coords.accuracy ?? undefined };
      setGps(g);
      setCourses(await api.available(g.lat, g.lng));
    } catch (e: any) {
      Alert.alert("Could not load", e.message ?? "Try again.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const current = courses[0];
  const initials = name.split(" ").map((s) => s[0]).slice(0, 2).join("").toUpperCase() || "ST";

  return (
    <Screen onRefresh={load} refreshing={loading}>
      <View style={styles.headerRow}>
        <View>
          <Text style={styles.welcome}>Welcome back</Text>
          <Text style={styles.name}>{name || "Student"}</Text>
        </View>
        <Avatar initials={initials} />
      </View>

      {!enrolled && (
        <Pressable style={styles.enrollBanner} onPress={onEnroll}>
          <Text style={styles.enrollTitle}>Enrol your face</Text>
          <Text style={styles.enrollSub}>Face is required before you can mark attendance · palm is optional. Tap to set up →</Text>
        </Pressable>
      )}

      {loading && !current ? (
        <ActivityIndicator color={colors.primary} style={{ marginTop: 40 }} />
      ) : current ? (
        <>
          <GradientCard>
            <View style={styles.courseTop}>
              <Text style={styles.badge}>KNUST · ATTENDANCE</Text>
              <Text style={styles.courseCode}>{current.course_code}</Text>
            </View>
            <Text style={styles.courseTitle}>{current.course_title}</Text>

            <Row label="Session" value={current.session_title} />
            <Row label="Lecturer" value={current.lecturer_name || "—"} />
            <Row label="Ends" value={new Date(current.ends_at).toLocaleString()} />
            <Row
              label="Proximity"
              value={current.in_range ? `Inside ${current.radius_m} m zone` : `${current.distance_m} m away`}
            />
          </GradientCard>

          <View style={styles.markCard}>
            <Text style={styles.markTitle}>Mark Your Attendance</Text>
            <Text style={styles.markSub}>
              Start {current.marked_start ? "✓" : "○"}   End {current.marked_end ? "✓" : "○"}   ·   {current.phase === "start" ? "START open" : current.phase === "end" ? "END open" : "check-in closed"}
            </Text>
            {current.status === "present" ? (
              <Text style={styles.presentBig}>✓ Present — marked at start and end</Text>
            ) : !enrolled ? (
              <Pressable style={styles.markBtn} onPress={onEnroll}>
                <Text style={styles.markBtnText}>Enrol to mark attendance  →</Text>
              </Pressable>
            ) : current.phase === "closed" ? (
              <Text style={styles.markInfo}>Check-in isn't open right now — wait for your lecturer.</Text>
            ) : (current.phase === "start" && current.marked_start) || (current.phase === "end" && current.marked_end) ? (
              <Text style={styles.markInfo}>
                {current.phase === "start" ? "Start marked ✓ — come back for the END check-in." : "End marked ✓."}
              </Text>
            ) : (
              <Pressable
                style={[styles.markBtn, !current.in_range && styles.markBtnDisabled]}
                disabled={!current.in_range || !gps}
                onPress={() => gps && onCheckIn(current, gps)}
              >
                <Text style={styles.markBtnText}>
                  {current.in_range ? `Mark ${current.phase === "start" ? "START" : "END"} with Face  →` : `Move within ${current.radius_m} m to mark`}
                </Text>
              </Pressable>
            )}
          </View>
        </>
      ) : (
        <View style={styles.empty}>
          <Text style={styles.emptyGlyph}>◎</Text>
          <Text style={styles.emptyText}>No live class near you right now.</Text>
          <Text style={styles.emptySub}>Pull down to refresh when a session starts.</Text>
        </View>
      )}
    </Screen>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.infoRow}>
      <Text style={styles.infoLabel}>{label}</Text>
      <Text style={styles.infoValue} numberOfLines={1}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  welcome: { color: colors.textMuted, fontSize: 13 },
  name: { color: colors.text, fontSize: 22, fontWeight: "800" },

  courseTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 6 },
  badge: { color: C.onGradient, fontSize: 10, fontWeight: "800", backgroundColor: C.overlayChip, paddingHorizontal: 8, paddingVertical: 4, borderRadius: radius.pill, overflow: "hidden" },
  courseCode: { color: C.onGradient, fontSize: 15, fontWeight: "800" },
  courseTitle: { color: C.onGradient, fontSize: 20, fontWeight: "800", marginBottom: space.md },
  infoRow: { flexDirection: "row", justifyContent: "space-between", backgroundColor: C.overlayChip, borderRadius: radius.sm, paddingHorizontal: 12, paddingVertical: 9, marginTop: 6 },
  infoLabel: { color: C.onGradientDim, fontSize: 13 },
  infoValue: { color: C.onGradient, fontSize: 13, fontWeight: "700", maxWidth: "62%" },

  markCard: { backgroundColor: colors.card, borderRadius: radius.lg, padding: space.lg, gap: 8, ...shadow.card },
  markTitle: { fontSize: 17, fontWeight: "800", color: colors.text },
  markSub: { fontSize: 13, color: colors.textMuted },
  markBtn: { backgroundColor: colors.primary, borderRadius: radius.md, padding: 16, alignItems: "center", marginTop: 6 },
  markBtnDisabled: { backgroundColor: "#E7A6B0" },
  markBtnText: { color: "#fff", fontWeight: "800", fontSize: 15 },
  presentBig: { color: colors.success, fontWeight: "800", fontSize: 16, marginTop: 8 },
  markInfo: { color: colors.textMuted, fontSize: 13.5, marginTop: 8 },

  enrollBanner: { backgroundColor: colors.primaryTint, borderRadius: radius.md, padding: space.md, borderWidth: 1, borderColor: "#F3C9D0" },
  enrollTitle: { color: colors.primary, fontWeight: "800", fontSize: 15 },
  enrollSub: { color: colors.primary, fontSize: 12.5, marginTop: 2, opacity: 0.85 },

  empty: { alignItems: "center", marginTop: 60, gap: 6 },
  emptyGlyph: { fontSize: 40, color: colors.textFaint },
  emptyText: { fontSize: 16, fontWeight: "700", color: colors.text },
  emptySub: { fontSize: 13, color: colors.textMuted },
});

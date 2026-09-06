import React, { useCallback, useEffect, useState } from "react";
import { View, Text, StyleSheet, Pressable } from "react-native";
import Screen from "../components/Screen";
import { StatusPill } from "../components/ui";
import { api, SemesterHistory } from "../api/client";
import { colors, radius, shadow, space } from "../theme";

export default function HistoryScreen({ onBack }: { onBack: () => void }) {
  const [data, setData] = useState<SemesterHistory[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try { setData(await api.history()); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  return (
    <Screen onRefresh={load} refreshing={loading}>
      <View style={styles.header}>
        <Pressable onPress={onBack} hitSlop={10}><Text style={styles.back}>‹</Text></Pressable>
        <View>
          <Text style={styles.title}>My Attendance</Text>
          <Text style={styles.sub}>Your record, by semester</Text>
        </View>
      </View>

      {data.map((sem) => (
        <View key={sem.semester} style={{ gap: space.sm }}>
          <Text style={styles.semester}>{sem.semester}</Text>
          {sem.items.map((it, i) => (
            <View key={i} style={styles.row}>
              <View style={{ flex: 1 }}>
                <Text style={styles.code}>{it.course_code} · {it.session_title}</Text>
                <Text style={styles.date}>{new Date(it.date).toLocaleString()}</Text>
              </View>
              <StatusPill status={it.status} />
            </View>
          ))}
        </View>
      ))}

      {data.length === 0 && !loading && <Text style={styles.empty}>No attendance records yet.</Text>}
    </Screen>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", gap: space.md },
  back: { fontSize: 30, color: colors.text, fontWeight: "700", marginRight: 2 },
  title: { fontSize: 20, fontWeight: "800", color: colors.text },
  sub: { fontSize: 13, color: colors.textMuted },
  semester: { fontSize: 14, fontWeight: "800", color: colors.primary, marginTop: space.md },
  row: { flexDirection: "row", alignItems: "center", backgroundColor: colors.card, borderRadius: radius.md, padding: space.md, ...shadow.card },
  code: { fontSize: 15, fontWeight: "700", color: colors.text },
  date: { fontSize: 12.5, color: colors.textMuted, marginTop: 2 },
  empty: { color: colors.textMuted, textAlign: "center", marginTop: 40 },
});

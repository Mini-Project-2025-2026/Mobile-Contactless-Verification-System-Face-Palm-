import React, { useCallback, useEffect, useState } from "react";
import { View, Text, StyleSheet } from "react-native";
import Screen from "../components/Screen";
import GradientCard from "../components/GradientCard";
import { ListRow } from "../components/ui";
import { api, DeviceItem } from "../api/client";
import { colors, radius, space, colors as C } from "../theme";

export default function DevicesScreen() {
  const [devices, setDevices] = useState<DeviceItem[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try { setDevices(await api.devices()); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const connected = devices.filter((d) => d.active).length;

  return (
    <Screen onRefresh={load} refreshing={loading}>
      <Text style={styles.h}>Logged In Devices</Text>

      <GradientCard>
        <View style={styles.summaryRow}>
          <View>
            <Text style={styles.bigNum}>{connected}</Text>
            <Text style={styles.summaryLabel}>Device{connected === 1 ? "" : "s"} Connected</Text>
          </View>
          <View style={styles.devGlyphWrap}><Text style={styles.devGlyph}>▢</Text></View>
        </View>
      </GradientCard>

      <Text style={styles.section}>Your Devices</Text>
      {devices.map((d) => (
        <ListRow
          key={d.device_uid}
          glyph="▢"
          title={d.name}
          subtitle={`${d.platform} · Last used ${d.active ? "Active now" : new Date(d.last_seen).toLocaleDateString()}`}
          right={
            d.active ? (
              <View style={styles.activePill}><Text style={styles.activeText}>Active</Text></View>
            ) : (
              <Text style={styles.dot}>•</Text>
            )
          }
        />
      ))}
      {devices.length === 0 && !loading && <Text style={styles.empty}>No devices registered yet.</Text>}
    </Screen>
  );
}

const styles = StyleSheet.create({
  h: { fontSize: 22, fontWeight: "800", color: colors.text },
  summaryRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  bigNum: { color: C.onGradient, fontSize: 34, fontWeight: "900" },
  summaryLabel: { color: C.onGradientDim, fontSize: 14, fontWeight: "600" },
  devGlyphWrap: { width: 54, height: 54, borderRadius: radius.md, backgroundColor: C.overlayChip, alignItems: "center", justifyContent: "center" },
  devGlyph: { color: "#fff", fontSize: 24 },
  section: { fontSize: 15, fontWeight: "800", color: colors.text, marginTop: space.sm },
  activePill: { backgroundColor: colors.successBg, paddingHorizontal: 10, paddingVertical: 4, borderRadius: radius.pill },
  activeText: { color: colors.success, fontWeight: "700", fontSize: 12 },
  dot: { color: colors.textFaint, fontSize: 22 },
  empty: { color: colors.textMuted, textAlign: "center", marginTop: 24 },
});

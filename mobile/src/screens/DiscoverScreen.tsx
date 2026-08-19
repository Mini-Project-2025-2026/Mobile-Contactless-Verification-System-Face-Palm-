import React, { useCallback, useEffect, useMemo, useState } from "react";
import { View, Text, StyleSheet, Pressable, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { WebView } from "react-native-webview";
import * as Location from "expo-location";
import { api, AvailableCourse } from "../api/client";
import { colors, radius, shadow } from "../theme";

type Gps = { lat: number; lng: number; accuracy_m?: number };

/** Keyless geofence map: Leaflet + OpenStreetMap tiles inside a WebView.
 *  Draws a translucent red circle + pin per active session and reports marker
 *  taps back to React Native. No Google Maps key required. */
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

  const html = useMemo(() => buildHtml(gps, sessions), [gps, sessions]);

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.pillWrap} pointerEvents="box-none">
        <View style={[styles.pill, anyInRange ? styles.pillIn : styles.pillOut]}>
          <Text style={styles.pillText}>{anyInRange ? "Inside Class Area" : "Outside Class Area"}</Text>
        </View>
        <Text style={styles.sessionCount}>◍ {sessions.length} Active Session{sessions.length === 1 ? "" : "s"}</Text>
      </View>

      {loading && !gps ? (
        <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
      ) : gps ? (
        <WebView
          key={html.length}
          originWhitelist={["*"]}
          source={{ html }}
          style={StyleSheet.absoluteFill}
          onMessage={(e) => {
            const id = Number(e.nativeEvent.data);
            const s = sessions.find((x) => x.session_id === id);
            if (s && s.in_range && gps) onCheckIn(s, gps);
          }}
        />
      ) : (
        <View style={styles.center}><Text style={styles.msg}>Enable location to see nearby classes.</Text></View>
      )}

      <View style={styles.fabs}>
        <Pressable style={styles.fab} onPress={load}><Text style={styles.fabGlyph}>↻</Text></Pressable>
      </View>
    </SafeAreaView>
  );
}

function buildHtml(gps: Gps | null, sessions: AvailableCourse[]): string {
  const center = gps ?? { lat: 6.6745, lng: -1.5716 };
  const data = JSON.stringify(sessions.map((s) => ({
    id: s.session_id, lat: s.center_lat, lng: s.center_lng, r: s.radius_m,
    code: s.course_code, inRange: s.in_range,
  })));
  const me = JSON.stringify({ lat: center.lat, lng: center.lng });
  return `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>html,body,#map{height:100%;margin:0}</style></head><body><div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
var me=${me},sessions=${data};
var map=L.map('map',{zoomControl:false}).setView([me.lat,me.lng],16);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap'}).addTo(map);
L.circleMarker([me.lat,me.lng],{radius:7,color:'#2563eb',fillColor:'#2563eb',fillOpacity:1}).addTo(map).bindTooltip('You');
sessions.forEach(function(s){
  L.circle([s.lat,s.lng],{radius:s.r,color:'#C41230',fillColor:'#C41230',fillOpacity:0.12,weight:2}).addTo(map);
  var m=L.marker([s.lat,s.lng]).addTo(map).bindTooltip(s.code+(s.inRange?' · tap to check in':' · out of range'));
  m.on('click',function(){ if(window.ReactNativeWebView) window.ReactNativeWebView.postMessage(String(s.id)); });
});
</script></body></html>`;
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  msg: { color: colors.textMuted },
  pillWrap: { position: "absolute", top: 52, left: 0, right: 0, zIndex: 10, alignItems: "center", gap: 6 },
  pill: { paddingHorizontal: 20, paddingVertical: 12, borderRadius: radius.md, ...shadow.card, width: "88%", alignItems: "center" },
  pillIn: { backgroundColor: colors.success },
  pillOut: { backgroundColor: colors.primary },
  pillText: { color: "#fff", fontWeight: "800" },
  sessionCount: { color: colors.text, fontWeight: "600", fontSize: 12, backgroundColor: colors.card, paddingHorizontal: 10, paddingVertical: 4, borderRadius: radius.pill, overflow: "hidden" },
  fabs: { position: "absolute", right: 16, bottom: 40, gap: 12 },
  fab: { width: 46, height: 46, borderRadius: 23, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center", ...shadow.card },
  fabGlyph: { color: "#fff", fontSize: 20, fontWeight: "700" },
});

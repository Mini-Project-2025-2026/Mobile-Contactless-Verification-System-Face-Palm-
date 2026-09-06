import React, { useEffect, useRef, useState } from "react";
import { View, Text, Pressable, StyleSheet, ActivityIndicator, TextInput } from "react-native";
import { CameraView, useCameraPermissions } from "expo-camera";
import { api, Modality } from "../api/client";
import { colors } from "../theme";

const SAMPLES = 3;

/** Face is compulsory, palm optional. The screen enrols face first; once face
 *  is done it offers palm as an optional add-on. */
export default function EnrollScreen({ onDone }: { onDone: (canMark: boolean) => void }) {
  const [perm, requestPerm] = useCameraPermissions();
  const [faceDone, setFaceDone] = useState(false);
  const [palmDone, setPalmDone] = useState(false);
  const [active, setActive] = useState<Modality | null>(null); // which modality we're capturing
  const [shots, setShots] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [grant, setGrant] = useState("");
  const [needGrant, setNeedGrant] = useState(false);
  const camRef = useRef<CameraView>(null);

  useEffect(() => { if (!perm?.granted) requestPerm(); }, [perm]);
  useEffect(() => {
    api.enrollStatus().then((s) => {
      setFaceDone(s.face_enrolled);
      setPalmDone(s.palm_enrolled);
      if (!s.face_enrolled) setActive("face");
    }).catch(() => setActive("face"));
  }, []);

  async function capture() {
    if (shots.length >= SAMPLES || busy) return;
    setBusy(true);
    try {
      const shot = await camRef.current?.takePictureAsync({ base64: true, quality: 0.6, skipProcessing: true });
      if (shot?.base64) setShots((s) => [...s, shot.base64!]);
    } finally { setBusy(false); }
  }

  async function submit() {
    if (!active) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.enroll({ modality: active, images: shots, grant_token: grant || undefined });
      setMsg(r.message);
      if (r.ok) {
        if (active === "face") setFaceDone(true);
        if (active === "palm") setPalmDone(true);
        setActive(null);
        setShots([]);
        setNeedGrant(false);
        setGrant("");
      } else if (r.code === "grant_required") {
        setNeedGrant(true); // keep shots; user enters the admin code and retries
      }
    } catch (e: any) {
      setMsg(e.message ?? "Enrolment failed.");
    } finally { setBusy(false); }
  }

  if (!perm) return <Center><ActivityIndicator color="#fff" /></Center>;
  if (!perm.granted) return <Center><Text style={styles.msg}>Camera permission is required to enrol.</Text></Center>;

  // Capturing view
  if (active) {
    const facing = active === "face" ? "front" : "back";
    const done = shots.length >= SAMPLES;
    return (
      <View style={styles.wrap}>
        <CameraView ref={camRef} style={styles.cam} facing={facing} />
        <View style={[styles.ring, active === "palm" && styles.ringPalm]} pointerEvents="none" />
        <View style={styles.overlay}>
          <Text style={styles.title}>Enrol your {active}{active === "face" ? " (required)" : " (optional)"}</Text>
          <Text style={styles.sub}>
            {active === "face" ? "Face the camera and capture 3 clear shots." : "Hold your open palm to the camera; capture 3 shots."}
          </Text>
          {msg && <Text style={styles.msg}>{msg}</Text>}
          <Text style={styles.count}>{shots.length} / {SAMPLES} captured</Text>
          {needGrant && (
            <TextInput style={styles.grantInput} placeholder="Enter admin one-time code" placeholderTextColor="#9fb0c8"
              autoCapitalize="characters" value={grant} onChangeText={setGrant} />
          )}
          {!done ? (
            <Pressable style={styles.btn} onPress={capture} disabled={busy}>
              {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.btnText}>Capture sample</Text>}
            </Pressable>
          ) : (
            <Pressable style={styles.btn} onPress={submit} disabled={busy}>
              {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.btnText}>Enrol {active}</Text>}
            </Pressable>
          )}
          <Pressable style={styles.ghost} onPress={() => { setActive(null); setShots([]); setMsg(null); }}>
            <Text style={styles.ghostText}>{shots.length ? "Reset" : "Back"}</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  // Status / hub view
  return (
    <Center>
      <Text style={styles.hubTitle}>Biometric enrolment</Text>
      {msg && <Text style={[styles.msg, { marginBottom: 6 }]}>{msg}</Text>}
      <View style={styles.statusRow}>
        <Text style={styles.statusItem}>{faceDone ? "✓" : "•"} Face <Text style={styles.req}>required</Text></Text>
        <Text style={styles.statusItem}>{palmDone ? "✓" : "•"} Palm <Text style={styles.opt}>optional</Text></Text>
      </View>

      {!faceDone && (
        <Pressable style={styles.btn} onPress={() => setActive("face")}><Text style={styles.btnText}>Enrol face (required)</Text></Pressable>
      )}
      {faceDone && !palmDone && (
        <Pressable style={styles.btn} onPress={() => setActive("palm")}><Text style={styles.btnText}>Add palm (optional)</Text></Pressable>
      )}
      {faceDone && (
        <Pressable style={styles.ghost} onPress={() => onDone(true)}><Text style={styles.ghostText}>Done</Text></Pressable>
      )}
      {!faceDone && (
        <Pressable style={styles.ghost} onPress={() => onDone(false)}><Text style={styles.ghostText}>Later</Text></Pressable>
      )}
    </Center>
  );
}

function Center({ children }: { children: React.ReactNode }) {
  return <View style={styles.center}>{children}</View>;
}

const styles = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: "#000" },
  cam: { ...StyleSheet.absoluteFillObject },
  ring: { position: "absolute", top: "16%", alignSelf: "center", width: 240, height: 300, borderRadius: 150, borderWidth: 3, borderColor: "rgba(255,255,255,0.55)" },
  ringPalm: { borderRadius: 24, height: 260 },
  overlay: { flex: 1, justifyContent: "flex-end", padding: 24, gap: 10, backgroundColor: "rgba(4,8,16,0.28)" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", gap: 12, backgroundColor: "#0b1220", padding: 24 },
  title: { color: "#fff", fontSize: 20, fontWeight: "800", textAlign: "center" },
  sub: { color: "#e5e9f0", fontSize: 13.5, textAlign: "center", marginBottom: 6 },
  hubTitle: { color: "#fff", fontSize: 22, fontWeight: "800" },
  statusRow: { flexDirection: "row", gap: 18, marginVertical: 8 },
  statusItem: { color: "#fff", fontSize: 16, fontWeight: "700" },
  req: { color: "#fca5a5", fontSize: 12, fontWeight: "700" },
  opt: { color: "#9fb0c8", fontSize: 12, fontWeight: "700" },
  count: { color: "#e5e9f0", textAlign: "center", fontWeight: "600" },
  btn: { backgroundColor: colors.primary, borderRadius: 14, padding: 16, alignItems: "center", alignSelf: "stretch" },
  btnText: { color: "#fff", fontWeight: "800", fontSize: 16 },
  ghost: { padding: 12, alignItems: "center" },
  ghostText: { color: "#cbd2dd", fontWeight: "600" },
  msg: { color: "#e5e9f0", textAlign: "center" },
  grantInput: { backgroundColor: "rgba(255,255,255,0.12)", color: "#fff", borderRadius: 10, padding: 12, fontSize: 16, textAlign: "center", letterSpacing: 2 },
});

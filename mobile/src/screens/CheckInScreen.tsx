import React, { useEffect, useRef, useState } from "react";
import { View, Text, Pressable, StyleSheet, ActivityIndicator } from "react-native";
import { CameraView, useCameraPermissions } from "expo-camera";
import { api, AvailableCourse, Modality, VerifyResult } from "../api/client";
import { colors } from "../theme";

type Gps = { lat: number; lng: number; accuracy_m?: number };
type Phase = "intro" | "capturing" | "submitting" | "done";

const FRAME_COUNT = 5;
const FRAME_GAP_MS = 450;

export default function CheckInScreen({
  course, gps, palmEnrolled, onDone,
}: {
  course: AvailableCourse; gps: Gps; palmEnrolled: boolean; onDone: () => void;
}) {
  const [perm, requestPerm] = useCameraPermissions();
  const [modality, setModality] = useState<Modality>("face");
  const [phase, setPhase] = useState<Phase>("intro");
  const [instruction, setInstruction] = useState("Turn your head slowly left and right");
  const [result, setResult] = useState<VerifyResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const camRef = useRef<CameraView>(null);

  useEffect(() => { if (!perm?.granted) requestPerm(); }, [perm]);

  async function run() {
    setError(null);
    setPhase("capturing");
    try {
      if (modality === "face") {
        const challenge = await api.challenge(course.session_id);
        if (challenge.instruction) setInstruction(challenge.instruction);
        const frames: string[] = [];
        for (let i = 0; i < FRAME_COUNT; i++) {
          const shot = await camRef.current?.takePictureAsync({ base64: true, quality: 0.5, skipProcessing: true });
          if (shot?.base64) frames.push(shot.base64);
          await new Promise((r) => setTimeout(r, FRAME_GAP_MS));
        }
        setPhase("submitting");
        const r = await api.verify({ session_id: course.session_id, modality: "face", token: challenge.token, frames, gps });
        setResult(r);
      } else {
        // palm: a single clear shot, no head-turn liveness
        const shot = await camRef.current?.takePictureAsync({ base64: true, quality: 0.6, skipProcessing: true });
        setPhase("submitting");
        const r = await api.verify({ session_id: course.session_id, modality: "palm", image: shot?.base64 ?? "", gps });
        setResult(r);
      }
      setPhase("done");
    } catch (e: any) {
      setError(e.message ?? "Verification failed.");
      setPhase("done");
    }
  }

  if (!perm) return <Center><ActivityIndicator color="#fff" /></Center>;
  if (!perm.granted) return <Center><Text style={styles.msg}>Camera permission is required to check in.</Text></Center>;

  const facing = modality === "face" ? "front" : "back";

  return (
    <View style={styles.wrap}>
      <CameraView ref={camRef} style={styles.cam} facing={facing} />
      <View style={[styles.ring, modality === "palm" && styles.ringPalm]} pointerEvents="none" />
      <View style={styles.overlay}>
        <Text style={styles.course}>{course.course_code} · {course.session_title}</Text>

        {phase === "intro" && (
          <>
            {palmEnrolled && (
              <View style={styles.toggle}>
                {(["face", "palm"] as Modality[]).map((m) => (
                  <Pressable key={m} style={[styles.tgl, modality === m && styles.tglOn]} onPress={() => setModality(m)}>
                    <Text style={[styles.tglText, modality === m && styles.tglTextOn]}>{m[0].toUpperCase() + m.slice(1)}</Text>
                  </Pressable>
                ))}
              </View>
            )}
            <Text style={styles.instruction}>
              {modality === "face" ? instruction : "Hold your open palm to the back camera"}
            </Text>
            <Pressable style={styles.btn} onPress={run}><Text style={styles.btnText}>Start {modality} check</Text></Pressable>
            <Pressable style={styles.ghost} onPress={onDone}><Text style={styles.ghostText}>Cancel</Text></Pressable>
          </>
        )}
        {phase === "capturing" && <Text style={styles.instruction}>{modality === "face" ? "Keep turning your head… hold steady" : "Hold still…"}</Text>}
        {phase === "submitting" && <Center><ActivityIndicator color="#fff" /><Text style={styles.msg}>Verifying…</Text></Center>}

        {phase === "done" && (
          <View style={styles.resultBox}>
            {error ? (
              <Text style={[styles.result, styles.deny]}>⚠ {error}</Text>
            ) : result?.ok ? (
              <>
                <Text style={[styles.result, styles.grant]}>
                  {result.status === "present" ? "✓ Present" : `✓ Mark ${result.marks_count}/${result.marks_required}`}
                </Text>
                <Text style={styles.msg}>{result.message}</Text>
              </>
            ) : (
              <>
                <Text style={[styles.result, styles.deny]}>✕ Not counted</Text>
                <Text style={styles.msg}>{result?.message ?? "Try again."}</Text>
              </>
            )}
            <Pressable style={styles.btn} onPress={onDone}><Text style={styles.btnText}>Done</Text></Pressable>
            {!error && result?.status !== "present" && (
              <Pressable style={styles.ghost} onPress={() => setPhase("intro")}>
                <Text style={styles.ghostText}>Check in again</Text>
              </Pressable>
            )}
          </View>
        )}
      </View>
    </View>
  );
}

function Center({ children }: { children: React.ReactNode }) {
  return <View style={styles.center}>{children}</View>;
}

const styles = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: "#000" },
  cam: { ...StyleSheet.absoluteFillObject },
  ring: { position: "absolute", top: "18%", alignSelf: "center", width: 240, height: 300, borderRadius: 150, borderWidth: 3, borderColor: "rgba(255,255,255,0.55)" },
  ringPalm: { borderRadius: 24, height: 260 },
  overlay: { flex: 1, justifyContent: "flex-end", padding: 24, gap: 12, backgroundColor: "rgba(4,8,16,0.28)" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", gap: 8, backgroundColor: "#000" },
  course: { color: "#eef1f6", fontWeight: "700", textAlign: "center" },
  toggle: { flexDirection: "row", backgroundColor: "rgba(255,255,255,0.15)", borderRadius: 999, padding: 4, alignSelf: "center" },
  tgl: { paddingHorizontal: 24, paddingVertical: 8, borderRadius: 999 },
  tglOn: { backgroundColor: "#fff" },
  tglText: { color: "#fff", fontWeight: "700" },
  tglTextOn: { color: colors.primaryDark },
  instruction: { color: "#fff", fontSize: 18, fontWeight: "700", textAlign: "center", marginBottom: 8 },
  msg: { color: "#e5e9f0", textAlign: "center" },
  resultBox: { backgroundColor: "rgba(20,10,14,0.92)", borderRadius: 18, padding: 20, gap: 10 },
  result: { fontSize: 24, fontWeight: "900", textAlign: "center" },
  grant: { color: "#4ade80" },
  deny: { color: "#fca5a5" },
  btn: { backgroundColor: colors.primary, borderRadius: 14, padding: 16, alignItems: "center" },
  btnText: { color: "#fff", fontWeight: "800", fontSize: 16 },
  ghost: { padding: 12, alignItems: "center" },
  ghostText: { color: "#cbd2dd", fontWeight: "600" },
});

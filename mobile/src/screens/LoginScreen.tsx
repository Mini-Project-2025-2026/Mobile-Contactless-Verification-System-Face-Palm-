import React, { useState } from "react";
import { View, Text, TextInput, Pressable, StyleSheet, ActivityIndicator, Alert } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { api, saveToken } from "../api/client";
import { getDeviceUid, deviceName, platform } from "../device";
import { colors, radius, space, shadow } from "../theme";

export default function LoginScreen({ onLoggedIn }: { onLoggedIn: (name: string) => void }) {
  const [studentId, setStudentId] = useState("20512345");
  const [password, setPassword] = useState("passw0rd");
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    try {
      const device_uid = await getDeviceUid();
      const r = await api.login({ student_id: studentId.trim(), password, device_uid, platform, device_name: deviceName });
      await saveToken(r.access_token);
      onLoggedIn(r.name);
    } catch (e: any) {
      Alert.alert("Sign in failed", e.message ?? "Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.wrap}>
        <View style={styles.brand}>
          <View style={styles.logo}><Text style={styles.logoText}>A</Text></View>
          <Text style={styles.title}>Attendance Verify</Text>
          <Text style={styles.sub}>Sign in with your student ID</Text>
        </View>

        <TextInput style={styles.input} placeholder="Student ID" placeholderTextColor={colors.textFaint}
          autoCapitalize="none" value={studentId} onChangeText={setStudentId} keyboardType="number-pad" />
        <TextInput style={styles.input} placeholder="Password" placeholderTextColor={colors.textFaint}
          secureTextEntry value={password} onChangeText={setPassword} />

        <Pressable style={[styles.btn, busy && styles.btnDisabled]} onPress={submit} disabled={busy}>
          {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.btnText}>Sign in</Text>}
        </Pressable>
        <Text style={styles.hint}>This phone becomes your registered device.</Text>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
  wrap: { flex: 1, justifyContent: "center", padding: space.xl, gap: space.md },
  brand: { alignItems: "center", marginBottom: space.lg, gap: 6 },
  logo: { width: 64, height: 64, borderRadius: 18, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center", ...shadow.card },
  logoText: { color: "#fff", fontSize: 34, fontWeight: "900" },
  title: { fontSize: 24, fontWeight: "800", color: colors.text },
  sub: { fontSize: 14, color: colors.textMuted },
  input: { backgroundColor: colors.card, color: colors.text, borderRadius: radius.md, padding: 14, fontSize: 16, borderWidth: 1, borderColor: colors.border },
  btn: { backgroundColor: colors.primary, borderRadius: radius.md, padding: 16, alignItems: "center", marginTop: 4, ...shadow.card },
  btnDisabled: { opacity: 0.6 },
  btnText: { color: "#fff", fontWeight: "700", fontSize: 16 },
  hint: { color: colors.textFaint, fontSize: 12, textAlign: "center", marginTop: 4 },
});

import React, { useEffect, useState } from "react";
import { StatusBar } from "expo-status-bar";
import { View, StyleSheet } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";
import TabBar, { TabKey } from "./src/components/TabBar";
import LoginScreen from "./src/screens/LoginScreen";
import HomeScreen from "./src/screens/HomeScreen";
import DiscoverScreen from "./src/screens/DiscoverScreen";
import DevicesScreen from "./src/screens/DevicesScreen";
import ProfileScreen from "./src/screens/ProfileScreen";
import HistoryScreen from "./src/screens/HistoryScreen";
import HelpScreen from "./src/screens/HelpScreen";
import CheckInScreen from "./src/screens/CheckInScreen";
import EnrollScreen from "./src/screens/EnrollScreen";
import { api, AvailableCourse, getToken, clearToken } from "./src/api/client";
import { colors } from "./src/theme";

type Gps = { lat: number; lng: number; accuracy_m?: number };
type Overlay =
  | { name: "checkin"; course: AvailableCourse; gps: Gps }
  | { name: "history" }
  | { name: "help" }
  | { name: "enroll" }
  | null;

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [name, setName] = useState("");
  const [tab, setTab] = useState<TabKey>("home");
  const [overlay, setOverlay] = useState<Overlay>(null);
  const [enrolled, setEnrolled] = useState(true); // "can mark" (face enrolled) — assume until status resolves
  const [palmEnrolled, setPalmEnrolled] = useState(false);

  useEffect(() => { getToken().then((t) => setAuthed(!!t)); }, []);

  const refreshEnrolled = () =>
    api.enrollStatus().then((s) => { setEnrolled(s.can_mark); setPalmEnrolled(s.palm_enrolled); }).catch(() => {});
  useEffect(() => { if (authed) refreshEnrolled(); }, [authed]);

  async function signOut() {
    await clearToken();
    setAuthed(false);
    setTab("home");
    setOverlay(null);
  }

  const openCheckIn = (course: AvailableCourse, gps: Gps) => setOverlay({ name: "checkin", course, gps });

  if (authed === null) return <SafeAreaProvider><View style={styles.root} /></SafeAreaProvider>;

  return (
    <SafeAreaProvider>
      <StatusBar style="dark" />
      <View style={styles.root}>
        {!authed ? (
          <LoginScreen onLoggedIn={(n) => { setName(n); setAuthed(true); }} />
        ) : (
          <>
            <View style={styles.body}>
              {tab === "home" && (
                <HomeScreen
                  name={name}
                  onCheckIn={openCheckIn}
                  enrolled={enrolled}
                  onEnroll={() => setOverlay({ name: "enroll" })}
                />
              )}
              {tab === "discover" && <DiscoverScreen onCheckIn={openCheckIn} />}
              {tab === "devices" && <DevicesScreen />}
              {tab === "profile" && (
                <ProfileScreen
                  name={name}
                  onOpenHistory={() => setOverlay({ name: "history" })}
                  onOpenHelp={() => setOverlay({ name: "help" })}
                  onEnroll={() => setOverlay({ name: "enroll" })}
                  onSignOut={signOut}
                />
              )}
            </View>
            <TabBar active={tab} onChange={setTab} />

            {overlay?.name === "checkin" && (
              <Overlayed>
                <CheckInScreen course={overlay.course} gps={overlay.gps} palmEnrolled={palmEnrolled} onDone={() => setOverlay(null)} />
              </Overlayed>
            )}
            {overlay?.name === "history" && (
              <Overlayed><HistoryScreen onBack={() => setOverlay(null)} /></Overlayed>
            )}
            {overlay?.name === "help" && (
              <Overlayed><HelpScreen onBack={() => setOverlay(null)} /></Overlayed>
            )}
            {overlay?.name === "enroll" && (
              <Overlayed>
                <EnrollScreen onDone={(ok) => { setOverlay(null); if (ok) refreshEnrolled(); }} />
              </Overlayed>
            )}
          </>
        )}
      </View>
    </SafeAreaProvider>
  );
}

function Overlayed({ children }: { children: React.ReactNode }) {
  return <View style={StyleSheet.absoluteFill}>{children}</View>;
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  body: { flex: 1 },
});

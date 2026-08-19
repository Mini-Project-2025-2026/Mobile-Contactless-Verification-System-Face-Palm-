import React from "react";
import { StyleSheet, View, ViewStyle } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { gradient, radius, shadow, space } from "../theme";

/** The crimson diagonal hero card used across the app (Home course card,
 *  Devices summary, Profile ID card). */
export default function GradientCard({ children, style }: { children: React.ReactNode; style?: ViewStyle }) {
  return (
    <View style={[styles.wrap, style]}>
      <LinearGradient colors={gradient.colors} start={gradient.start} end={gradient.end} style={styles.grad}>
        {children}
      </LinearGradient>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { borderRadius: radius.lg, ...shadow.card },
  grad: { borderRadius: radius.lg, padding: space.lg },
});

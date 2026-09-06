/** Stable per-install device id (backs the one-device policy). */
import * as SecureStore from "expo-secure-store";
import * as Device from "expo-device";
import { Platform } from "react-native";

const KEY = "attendance.device_uid";

export async function getDeviceUid(): Promise<string> {
  let uid = await SecureStore.getItemAsync(KEY);
  if (!uid) {
    uid = `${Platform.OS}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    await SecureStore.setItemAsync(KEY, uid);
  }
  return uid;
}

export const deviceName = Device.deviceName ?? `${Platform.OS} device`;
export const platform = Platform.OS;

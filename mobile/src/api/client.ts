/**
 * Thin typed client for the Attendance-Verify backend.
 *
 * The biometric API key never lives here — the app only ever talks to our own
 * backend, which orchestrates verification server-side.
 */
import * as SecureStore from "expo-secure-store";

// For a physical device on the same Wi-Fi, set this to your machine's LAN IP.
export const API_BASE = "https://attendance-verify-api-fd04b68b7941.herokuapp.com"; // live Heroku backend

const TOKEN_KEY = "attendance.jwt";

export async function saveToken(t: string) {
  await SecureStore.setItemAsync(TOKEN_KEY, t);
}
export async function getToken() {
  return SecureStore.getItemAsync(TOKEN_KEY);
}
export async function clearToken() {
  await SecureStore.deleteItemAsync(TOKEN_KEY);
}

async function request<T>(path: string, opts: RequestInit = {}, auth = true): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json", ...(opts.headers as any) };
  if (auth) {
    const token = await getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }
  const res = await fetch(`${API_BASE}${path}`, { ...opts, headers });
  const text = await res.text();
  const body = text ? JSON.parse(text) : {};
  if (!res.ok) {
    const detail = body?.detail ?? body?.message ?? res.statusText;
    throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail), res.status, body);
  }
  return body as T;
}

export class ApiError extends Error {
  constructor(message: string, public status: number, public body: any) {
    super(message);
  }
}

// ---- types ----
export interface LoginResult { access_token: string; student_id: string; name: string; }
export interface AvailableCourse {
  session_id: number; course_code: string; course_title: string; session_title: string;
  lecturer_name: string; distance_m: number; radius_m: number; in_range: boolean; ends_at: string;
  center_lat: number; center_lng: number;
  phase: "start" | "end" | "closed"; marked_start: boolean; marked_end: boolean;
  status: "absent" | "partial" | "present";
}
export interface Challenge { token: string; instruction: string; modality: string; active: boolean; }
export interface VerifyResult {
  ok: boolean; status: "absent" | "partial" | "present"; marks_count: number;
  marks_required: number; distance_m: number; score: number; code: string; message: string;
}
export interface DeviceItem { device_uid: string; platform: string; name: string; active: boolean; last_seen: string; }
export interface Profile {
  student_id: string; name: string; reference_no: string;
  programme: string; year_group: string; class_group: string; semester: string; enrolled: boolean;
}
export type Modality = "face" | "palm";
export interface EnrollStatus { face_enrolled: boolean; palm_enrolled: boolean; can_mark: boolean; }
export interface EnrollResult {
  ok: boolean; enrolled: number; of: number; samples: number; modality: Modality; message: string; code: string;
}
export interface AttendanceItem {
  course_code: string; course_title: string; session_title: string;
  date: string; status: "absent" | "partial" | "present";
}
export interface SemesterHistory { semester: string; items: AttendanceItem[]; }

// ---- endpoints ----
export const api = {
  login: (p: { student_id: string; password: string; device_uid: string; platform: string; device_name: string }) =>
    request<LoginResult>("/api/auth/login", { method: "POST", body: JSON.stringify(p) }, false),

  available: (lat: number, lng: number) =>
    request<AvailableCourse[]>(`/api/courses/available?lat=${lat}&lng=${lng}`),

  challenge: (session_id: number) =>
    request<Challenge>("/api/checkin/challenge", { method: "POST", body: JSON.stringify({ session_id }) }),

  verify: (p: {
    session_id: number; gps: { lat: number; lng: number; accuracy_m?: number };
    modality?: Modality; token?: string; frames?: string[]; image?: string;
  }) => request<VerifyResult>("/api/checkin/verify", { method: "POST", body: JSON.stringify(p) }),

  profile: () => request<Profile>("/api/profile"),
  devices: () => request<DeviceItem[]>("/api/devices"),
  history: () => request<SemesterHistory[]>("/api/attendance/history"),

  enrollStatus: () => request<EnrollStatus>("/api/enroll/status"),
  enroll: (p: { modality: Modality; images: string[]; source?: string; grant_token?: string }) =>
    request<EnrollResult>("/api/enroll", { method: "POST", body: JSON.stringify(p) }),
};

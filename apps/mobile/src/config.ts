/** API address for the mobile client. Android emulators reach the host loopback at 10.0.2.2. */
import { Platform } from "react-native";

export const API_BASE_URL: string =
  (globalThis as { ATTA_API_URL?: string }).ATTA_API_URL ??
  (Platform.OS === "android" ? "http://10.0.2.2:8800" : "http://127.0.0.1:8800");

import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, SafeAreaView, ScrollView, StyleSheet, Text, useColorScheme, View } from "react-native";
import { AttaApiError, AttaClient, summarizeReadiness } from "@atta/api_client";
import { color, space, statusColor } from "@atta/design_system";
import type { ReadinessReport } from "@atta/shared_types";
import { API_BASE_URL } from "./src/config";

const client = new AttaClient({ baseUrl: API_BASE_URL, timeoutMs: 5000 });

export default function App(): React.JSX.Element {
  const theme = useColorScheme() === "dark" ? "dark" : "light";
  const c = color[theme];
  const [report, setReport] = useState<ReadinessReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setReport((await client.ready()).report);
      setError(null);
    } catch (err) {
      setReport(null);
      setError(err instanceof AttaApiError ? `${err.code}: ${err.message}` : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  const summary = report ? summarizeReadiness(report) : null;

  return (
    <SafeAreaView style={[styles.root, { backgroundColor: c.bg }]}>
      <ScrollView contentContainerStyle={styles.body}>
        <Text style={[styles.h1, { color: c.text }]}>ATTa</Text>
        <Text style={{ color: c.muted }}>Platform status — {API_BASE_URL}</Text>
        <View style={[styles.card, { backgroundColor: c.surface, borderColor: c.border }]}>
          {loading && <ActivityIndicator />}
          {error && <Text style={{ color: c.danger }}>Backend unreachable — {error}</Text>}
          {summary && report && (
            <>
              <Text style={[styles.status, { color: statusColor(theme, report.status) }]}>{summary.label}</Text>
              {report.dependencies.map((d) => (
                <Text key={d.name} style={{ color: c.text }}>
                  {d.name}{d.essential ? " (essential)" : ""}: <Text style={{ color: statusColor(theme, d.status) }}>{d.status}</Text>
                </Text>
              ))}
            </>
          )}
        </View>
        <Pressable accessibilityRole="button" onPress={refresh} style={[styles.button, { backgroundColor: c.accent }]}>
          <Text style={styles.buttonText}>Refresh</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  body: { padding: space.lg },
  h1: { fontSize: 24, fontWeight: "700", marginBottom: space.xs },
  card: { borderWidth: 1, borderRadius: 10, padding: space.lg, marginTop: space.lg, gap: space.sm },
  status: { fontSize: 18, fontWeight: "700" },
  button: { marginTop: space.lg, borderRadius: 10, padding: space.md, alignItems: "center" },
  buttonText: { color: "#ffffff", fontWeight: "600" },
});

// Firebase in Studio: Firestore is the real-time feed for field alerts (the API mirrors every alert there),
// and Firebase Anonymous Auth gives each browser a private space for chat backups.
// Config is the public web-app config (security comes from firestore.rules), passed as VITE_FIREBASE_CONFIG.
import { initializeApp, type FirebaseApp } from "firebase/app";
import { collection, getFirestore, onSnapshot, query, where, type Firestore } from "firebase/firestore";
import type { Alert } from "./api";

let app: FirebaseApp | null = null;
let db: Firestore | null = null;

function init(): Firestore | null {
  if (db) return db;
  const raw = import.meta.env.VITE_FIREBASE_CONFIG as string | undefined;
  if (!raw) return null;
  try {
    app = initializeApp(JSON.parse(raw));
    db = getFirestore(app);
  } catch {
    db = null;
  }
  return db;
}

export const firebaseApp = () => (init(), app);
export const firestore = () => init();

/** Live alerts for one plantation, newest first. Returns an unsubscribe function, or null if Firestore isn't configured. */
export function watchAlerts(plantationId: string, onAlerts: (a: Alert[]) => void, onError: () => void): (() => void) | null {
  const d = init();
  if (!d) return null;
  return onSnapshot(
    query(collection(d, "alerts"), where("plantation_id", "==", plantationId)),
    (snap) => {
      const items = snap.docs.map((doc) => {
        const x = doc.data();
        const created = x.created_at?.toDate ? x.created_at.toDate().toISOString() : String(x.created_at);
        return {
          alert_id: doc.id, plantation_id: x.plantation_id, created_at: created, type: x.type, severity: x.severity,
          title: x.title, message_en: x.message_en, message_local: x.message_local, evidence: {},
          has_audio: !!x.has_audio, delivered: !!x.delivered,
        } as Alert;
      });
      items.sort((a, b) => b.created_at.localeCompare(a.created_at));
      onAlerts(items);
    },
    () => onError(),
  );
}

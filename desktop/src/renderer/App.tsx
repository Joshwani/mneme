import { useEffect, useState } from "react";
import type { ServerStatus } from "../shared/contracts";
import { CatalogScreen } from "./screens/CatalogScreen";
import { CredentialsScreen } from "./screens/CredentialsScreen";
import { OverviewScreen } from "./screens/OverviewScreen";
import { SourcesScreen } from "./screens/SourcesScreen";
import mnemeIcon from "./assets/mneme-icon.png";

type Screen = "overview" | "catalog" | "sources" | "credentials";

const navigation: Array<{ id: Screen; label: string; icon: string }> = [
  { id: "overview", label: "Overview", icon: "⌁" },
  { id: "catalog", label: "Catalog", icon: "⌕" },
  { id: "sources", label: "Sources", icon: "＋" },
  { id: "credentials", label: "Auth Profiles", icon: "◈" },
];

export function App() {
  const [screen, setScreen] = useState<Screen>("overview");
  const [server, setServer] = useState<ServerStatus>({ state: "starting" });

  useEffect(() => {
    let active = true;
    const refresh = () => {
      void window.mneme.server.status().then((next) => {
        if (active) setServer(next);
      });
    };
    refresh();
    const timer = window.setInterval(refresh, 2_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <img className="brand-mark" src={mnemeIcon} alt="" />
          <div>
            <strong>Mneme</strong>
            <span>Desktop companion</span>
          </div>
        </div>
        <nav>
          {navigation.map((item) => (
            <button
              className={screen === item.id ? "nav-item active" : "nav-item"}
              key={item.id}
              onClick={() => setScreen(item.id)}
            >
              <span className="nav-icon">{item.icon}</span>
              {item.label}
            </button>
          ))}
        </nav>
        <div className="server-pill">
          <i className={`status-dot ${server.state}`} />
          <div>
            <span>Local server</span>
            <strong>{server.state}</strong>
          </div>
        </div>
      </aside>
      <main className="content">
        {screen === "overview" && <OverviewScreen />}
        {screen === "catalog" && <CatalogScreen />}
        {screen === "sources" && <SourcesScreen />}
        {screen === "credentials" && <CredentialsScreen />}
      </main>
    </div>
  );
}

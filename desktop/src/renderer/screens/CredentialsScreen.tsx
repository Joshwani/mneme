import { useCallback, useEffect, useState, type FormEvent } from "react";
import type { AuthProfile, CredentialMetadata } from "../../shared/contracts";
import { EmptyState, ErrorState, LoadingState } from "../components/States";

export function CredentialsScreen() {
  const [credentials, setCredentials] = useState<CredentialMetadata[]>([]);
  const [profiles, setProfiles] = useState<AuthProfile[]>([]);
  const [editing, setEditing] = useState<CredentialMetadata>();
  const [editingProfile, setEditingProfile] = useState<AuthProfile>();
  const [label, setLabel] = useState("");
  const [value, setValue] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [profileName, setProfileName] = useState("");
  const [providerDomain, setProviderDomain] = useState("");
  const [credentialEnv, setCredentialEnv] = useState("");
  const [authType, setAuthType] = useState<"bearer" | "api_key">("bearer");
  const [apiKeyName, setApiKeyName] = useState("X-API-Key");
  const [error, setError] = useState<unknown>();

  const load = useCallback(() => {
    setLoading(true);
    setError(undefined);
    void Promise.all([window.mneme.credentials.list(), window.mneme.authProfiles.list()])
      .then(([nextCredentials, nextProfiles]) => {
        setCredentials(nextCredentials);
        setProfiles(nextProfiles);
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  const reset = () => {
    setEditing(undefined);
    setLabel("");
    setValue("");
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(undefined);
    try {
      if (editing) {
        await window.mneme.credentials.update({
          id: editing.id,
          label,
          ...(value ? { value } : {}),
        });
      } else {
        await window.mneme.credentials.create({ label, value });
      }
      reset();
      load();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setSaving(false);
    }
  };

  const edit = (credential: CredentialMetadata) => {
    setEditing(credential);
    setLabel(credential.label);
    setValue("");
  };

  const remove = async (credential: CredentialMetadata) => {
    if (!window.confirm(`Delete “${credential.label}”? Calls using its environment name will stop authenticating.`)) {
      return;
    }
    setError(undefined);
    try {
      await window.mneme.credentials.remove(credential.id);
      if (editing?.id === credential.id) reset();
      load();
    } catch (nextError) {
      setError(nextError);
    }
  };

  const submitProfile = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(undefined);
    try {
      const input = {
        name: profileName,
        providerDomain,
        authType,
        credentialEnv,
        ...(authType === "api_key" ? { apiKeyName } : {}),
        allowMethods: ["GET", "POST", "PUT", "PATCH", "DELETE"],
      };
      if (editingProfile) {
        await window.mneme.authProfiles.update(editingProfile.name, input);
      } else {
        await window.mneme.authProfiles.create(input);
      }
      resetProfile();
      load();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setSaving(false);
    }
  };

  const resetProfile = () => {
    setEditingProfile(undefined);
    setProfileName("");
    setProviderDomain("");
    setCredentialEnv("");
    setAuthType("bearer");
    setApiKeyName("X-API-Key");
  };

  const editProfile = (profile: AuthProfile) => {
    setEditingProfile(profile);
    setProfileName(profile.name);
    setProviderDomain(profile.providerDomain ?? "");
    setCredentialEnv(profile.credentialEnv ?? "");
    setAuthType(profile.authType === "api_key" ? "api_key" : "bearer");
    setApiKeyName(profile.apiKeyName ?? "X-API-Key");
  };

  const removeProfile = async (profile: AuthProfile) => {
    if (!window.confirm(`Delete auth profile “${profile.name}”?`)) return;
    try {
      await window.mneme.authProfiles.remove(profile.name);
      if (editingProfile?.name === profile.name) resetProfile();
      load();
    } catch (nextError) {
      setError(nextError);
    }
  };

  return (
    <section>
      <header className="page-header compact">
        <div>
          <span className="eyebrow">OS-encrypted secrets</span>
          <h1>Auth Profiles</h1>
          <p>Store credentials locally and reference their generated environment names.</p>
        </div>
      </header>
      <div className="section-heading">
        <h2>Connection profiles</h2>
        <span>{profiles.length} configured</span>
      </div>
      <div className="profile-layout">
        <div className="credential-list">
          {profiles.length ? (
            profiles.map((profile) => (
              <article className="credential-row" key={profile.name}>
                <div className="credential-icon">↗</div>
                <div className="credential-copy">
                  <strong>{profile.name}</strong>
                  <code>{profile.providerDomain ?? "No provider domain"}</code>
                  <span>
                    {profile.authType ?? "auth"} · {profile.allowMethods.join(", ")}
                  </span>
                </div>
                <button className="button secondary" onClick={() => editProfile(profile)}>
                  Edit
                </button>
                <button className="button danger" onClick={() => void removeProfile(profile)}>
                  Delete
                </button>
              </article>
            ))
          ) : (
            <EmptyState
              title="No auth profiles"
              detail="Connect a provider policy to an OS-encrypted credential."
            />
          )}
        </div>
        <form className="form-card profile-form" onSubmit={(event) => void submitProfile(event)}>
          <label>
            Profile name
            <input
              onChange={(event) => setProfileName(event.target.value)}
              placeholder="github"
              required
              disabled={editingProfile !== undefined}
              value={profileName}
            />
          </label>
          {authType === "api_key" && (
            <label>
              Header name
              <input
                onChange={(event) => setApiKeyName(event.target.value)}
                placeholder="X-API-Key"
                required
                value={apiKeyName}
              />
            </label>
          )}
          <label>
            Provider domain
            <input
              onChange={(event) => setProviderDomain(event.target.value)}
              placeholder="api.github.com"
              required
              value={providerDomain}
            />
          </label>
          <label>
            Authentication
            <select onChange={(event) => setAuthType(event.target.value as typeof authType)} value={authType}>
              <option value="bearer">Bearer token</option>
              <option value="api_key">API key header</option>
            </select>
          </label>
          <label>
            Stored credential
            <select
              onChange={(event) => setCredentialEnv(event.target.value)}
              required
              value={credentialEnv}
            >
              <option value="">Choose credential…</option>
              {credentials.map((credential) => (
                <option key={credential.id} value={credential.envName}>
                  {credential.label}
                </option>
              ))}
            </select>
          </label>
          <div className="form-actions">
            {editingProfile && (
              <button className="button secondary" onClick={resetProfile} type="button">
                Cancel
              </button>
            )}
            <button className="button primary" disabled={saving || !credentials.length}>
              {editingProfile ? "Save profile" : "Add profile"}
            </button>
          </div>
        </form>
      </div>
      <div className="section-heading">
        <h2>Encrypted credentials</h2>
        <span>{credentials.length} stored</span>
      </div>
      <div className="credential-layout">
        <div>
          {error !== undefined && <ErrorState error={error} retry={load} />}
          {loading ? (
            <LoadingState label="Loading auth profiles" />
          ) : credentials.length ? (
            <div className="credential-list">
              {credentials.map((credential) => (
                <article className="credential-row" key={credential.id}>
                  <div className="credential-icon">•••</div>
                  <div className="credential-copy">
                    <strong>{credential.label}</strong>
                    <code>{credential.envName}</code>
                    <span>Updated {new Date(credential.updatedAt).toLocaleString()}</span>
                  </div>
                  <button className="button secondary" onClick={() => edit(credential)}>
                    Edit
                  </button>
                  <button className="button danger" onClick={() => void remove(credential)}>
                    Delete
                  </button>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState
              title="No stored credentials"
              detail="Add a token or API key. Mneme receives it only through the supervised sidecar environment."
            />
          )}
        </div>
        <form className="form-card" onSubmit={(event) => void submit(event)}>
          <div>
            <span className="eyebrow">{editing ? "Update secret" : "New secret"}</span>
            <h2>{editing ? editing.label : "Add credential"}</h2>
          </div>
          <label>
            Display name
            <input
              maxLength={120}
              onChange={(event) => setLabel(event.target.value)}
              placeholder="GitHub API token"
              required
              value={label}
            />
          </label>
          <label>
            Secret value
            <input
              autoComplete="off"
              onChange={(event) => setValue(event.target.value)}
              placeholder={editing ? "Leave blank to keep current value" : "Paste token or API key"}
              required={!editing}
              type="password"
              value={value}
            />
          </label>
          <p className="form-hint">
            The raw value is encrypted with macOS Keychain, Windows DPAPI, or Linux Secret
            Service and is never returned to this screen.
          </p>
          <div className="form-actions">
            {editing && (
              <button className="button secondary" onClick={reset} type="button">
                Cancel
              </button>
            )}
            <button className="button primary" disabled={saving} type="submit">
              {saving ? "Saving…" : editing ? "Save changes" : "Store credential"}
            </button>
          </div>
        </form>
      </div>
      <div className="privacy-note compact-note">
        <span>i</span>
        <div>
          <strong>Profile wiring</strong>
          <p>
            Copy a generated environment name into an Mneme auth profile’s token_env,
            api_key_env, username_env, or password_env field. OAuth is intentionally not
            handled by this desktop release.
          </p>
        </div>
      </div>
    </section>
  );
}

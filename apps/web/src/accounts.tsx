import { useEffect, useState } from "react";
import { api, type Data } from "./api";

type Props = { fail: (error: unknown) => void };
export function AccountSettings({ fail }: Props) {
  const [status, setStatus] = useState<Data | null>(null),
    [busy, setBusy] = useState(""),
    [message, setMessage] = useState("");
  const [mail, setMail] = useState<Data | null>(null);
  const load = async () => {
    const [accounts, email] = await Promise.all([
      api("/accounts"),
      api("/email"),
    ]);
    setStatus(accounts);
    setMail(email);
  };
  async function enableEmail(accountId: string) {
    setBusy(accountId);
    try {
      const result = await api(
        `/accounts/${accountId}/email/authorize`,
        "POST",
        {},
      );
      location.assign(result.url);
    } catch (e) {
      fail(e);
      setBusy("");
    }
  }
  useEffect(() => {
    load().catch(fail);
  }, []);
  async function connect(provider: string) {
    setBusy(provider);
    try {
      const result = await api(
        `/accounts/providers/${provider}/authorize`,
        "POST",
        {},
      );
      location.assign(result.url);
    } catch (e) {
      fail(e);
      setBusy("");
    }
  }
  const outcome = new URLSearchParams(location.search).get("account");
  return (
    <div className="card settings-form">
      <h3>Connected accounts</h3>
      <p>
        Connect Google or Microsoft calendars. Leam stores account credentials
        encrypted on your host. Gmail reading is a separate optional connection;
        no email sending permissions are requested.
      </p>
      {outcome === "connected" && (
        <p role="status">
          Account connected. Calendar synchronization is the next step.
        </p>
      )}
      {outcome === "email-connected" && (
        <p role="status">
          Read-only email connected. Sync the inbox below to show recent email
          in Today.
        </p>
      )}
      {outcome === "email-denied" && (
        <p role="status">
          Mailbox reading was not granted. Your calendar connection is
          unchanged.
        </p>
      )}
      {outcome === "cancelled" && (
        <p role="status">
          Account connection was cancelled. You can try again.
        </p>
      )}
      {(status?.providers || []).map((provider: Data) => (
        <ProviderSetup
          key={provider.id + ":" + provider.revision}
          provider={provider}
          fail={fail}
          changed={load}
          connect={() => connect(provider.id)}
          connecting={!!busy}
        />
      ))}
      {(status?.items || []).map((account: Data) => (
        <article className="reminder-card" key={account.id}>
          <h4>{account.identity}</h4>
          <p>
            {account.provider === "google" ? "Google" : "Microsoft"} ·{" "}
            {account.state === "connected"
              ? "Account access connected"
              : "Reconnect required"}
          </p>
          <p>
            Account access last checked:{" "}
            {account.checkedAt
              ? new Date(account.checkedAt * 1000).toLocaleString()
              : "never"}
            . This is separate from calendar sync.
          </p>
          {account.error && <p role="alert">{account.error}</p>}
          {account.provider === "google" && (
            <div className="email-connection">
              <h5>Read-only Gmail</h5>
              <p>
                Optional: let Leam show recent inbox messages in Today. Google
                grants read access to your mailbox; this version fetches at most
                20 messages from the last 30 days, using sender, subject,
                snippet and read state. It does not send, archive, mark read or
                delete email.
              </p>
              <p>
                {account.email?.granted
                  ? "Mailbox reading granted."
                  : "Email is not connected. Calendar access does not include your inbox."}
              </p>
              <button
                className="secondary"
                disabled={!!busy}
                onClick={() => enableEmail(account.id)}
              >
                {account.email?.granted
                  ? "Reconnect read-only email"
                  : "Enable read-only email"}
              </button>
              {account.email?.granted && (
                <>
                  <p>
                    Last email sync:{" "}
                    {(() => {
                      const snapshot = mail?.accounts?.find(
                        (item: Data) => item.accountId === account.id,
                      );
                      return snapshot?.syncedAt
                        ? new Date(snapshot.syncedAt * 1000).toLocaleString() +
                            (snapshot.stale ? " (stale)" : "")
                        : "never";
                    })()}
                  </p>
                  {mail?.accounts?.find(
                    (item: Data) => item.accountId === account.id,
                  )?.error && (
                    <p role="alert">
                      {
                        mail?.accounts?.find(
                          (item: Data) => item.accountId === account.id,
                        )?.error
                      }
                    </p>
                  )}
                  <button
                    className="secondary"
                    disabled={!!busy || account.email?.state !== "connected"}
                    onClick={async () => {
                      setBusy(account.id);
                      try {
                        const result = await api(
                          `/email/accounts/${account.id}/sync`,
                          "POST",
                          {},
                        );
                        setMessage(
                          result.error ||
                            (result.truncated
                              ? "Recent inbox synced. More messages are available in Gmail."
                              : "Recent inbox synced."),
                        );
                      } catch (e) {
                        fail(e);
                      } finally {
                        await load().catch(fail);
                        setBusy("");
                      }
                    }}
                  >
                    Sync recent inbox
                  </button>
                  <button
                    className="secondary"
                    disabled={!!busy}
                    onClick={async () => {
                      setBusy(account.id);
                      try {
                        await api(`/email/accounts/${account.id}`, "DELETE");
                        setMessage(
                          "Email access and cached messages removed from Leam. Calendar access is unchanged. Google grants and older backups are separate.",
                        );
                      } catch (e) {
                        fail(e);
                      } finally {
                        await load().catch(fail);
                        setBusy("");
                      }
                    }}
                  >
                    Remove email from Leam
                  </button>
                </>
              )}
            </div>
          )}
          <div className="actions">
            <button
              className="secondary"
              disabled={!!busy}
              onClick={() => connect(account.provider)}
            >
              Reconnect {account.identity}
            </button>
            <button
              className="secondary"
              disabled={!!busy}
              onClick={async () => {
                setBusy(account.id);
                try {
                  await api(`/accounts/${account.id}/verify`, "POST", {});
                  setMessage("Account access verified.");
                } catch (e) {
                  fail(e);
                } finally {
                  await load().catch(fail);
                  setBusy("");
                }
              }}
            >
              Check account access
            </button>
            <button
              className="danger"
              disabled={!!busy}
              onClick={async () => {
                setBusy(account.id);
                try {
                  await api(`/accounts/${account.id}`, "DELETE");
                  setMessage(
                    "Account removed from Leam. You can also revoke the app grant in your Google or Microsoft account permissions.",
                  );
                } catch (e) {
                  fail(e);
                } finally {
                  await load().catch(fail);
                  setBusy("");
                }
              }}
            >
              Disconnect {account.identity}
            </button>
          </div>
        </article>
      ))}
      {message && <p role="status">{message}</p>}
    </div>
  );
}

function ProviderSetup({
  provider,
  fail,
  changed,
  connect,
  connecting,
}: Props & {
  provider: Data;
  changed: () => Promise<void>;
  connect: () => Promise<void>;
  connecting: boolean;
}) {
  const [clientId, setClientId] = useState(provider.clientId || ""),
    [secret, setSecret] = useState(""),
    [busy, setBusy] = useState(false);
  const name = provider.id === "google" ? "Google" : "Microsoft";
  const callback = location.origin + provider.callbackPath;
  return (
    <fieldset disabled={busy || connecting} className="account-provider">
      <legend>{name}</legend>
      <details open={!provider.secretConfigured}>
        <summary>{name} application setup</summary>
        <p>
          Create a Web application OAuth client in{" "}
          {name === "Google" ? (
            <a
              href="https://console.cloud.google.com/auth/clients"
              target="_blank"
              rel="noreferrer"
            >
              Google Auth Platform
            </a>
          ) : (
            <a
              href="https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade"
              target="_blank"
              rel="noreferrer"
            >
              Microsoft Entra app registrations
            </a>
          )}{" "}
          and register this exact redirect URI:
        </p>
        <code className="breakable">{callback}</code>
        <p>
          {name === "Google"
            ? "Enable Google Calendar API (and Gmail API if you opt into email) and add your account as a test user while the consent app is in testing. Google test-mode refresh grants may require periodic reconnection."
            : "Choose organizational and personal Microsoft accounts if you need both. Add the redirect URI under Web, then create a client secret. Your organization may require administrator consent."}
        </p>
        <label>
          {name} client ID
          <input
            aria-label={name + " client ID"}
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
            autoComplete="off"
          />
        </label>
        <label>
          {name} client secret
          <input
            aria-label={name + " client secret"}
            type="password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            autoComplete="new-password"
            placeholder={
              provider.secretConfigured
                ? "Leave blank to keep saved secret"
                : "Application secret"
            }
          />
        </label>
        <button
          className="secondary"
          disabled={!clientId.trim() || (!secret && !provider.secretConfigured)}
          onClick={async () => {
            setBusy(true);
            try {
              await api(`/accounts/providers/${provider.id}`, "PUT", {
                revision: provider.revision,
                clientId,
                clientSecret: secret || undefined,
              });
              setSecret("");
              await changed();
            } catch (e) {
              fail(e);
            } finally {
              setBusy(false);
            }
          }}
        >
          Save {name} application
        </button>
        {provider.secretConfigured && (
          <button
            className="danger"
            onClick={async () => {
              setBusy(true);
              try {
                await api(`/accounts/providers/${provider.id}`, "DELETE");
                await changed();
              } catch (e) {
                fail(e);
              } finally {
                setBusy(false);
              }
            }}
          >
            Remove {name} application and disconnect its accounts
          </button>
        )}
        <p>
          Removing credentials deletes current Leam connections. Provider grants
          and older backups are separate; manage grants in your provider's
          account permissions.
        </p>
      </details>
      <p>
        Requested access: account identity, calendar reading and event editing.
        The provider shows the consent details before connecting.
      </p>
      <button
        className="primary"
        disabled={!provider.secretConfigured}
        onClick={connect}
      >
        Connect {name}
      </button>
    </fieldset>
  );
}

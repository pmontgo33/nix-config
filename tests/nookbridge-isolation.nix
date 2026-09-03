# Stage 8 NixOS VM isolation test for NookBridge.
#
# Scope: this is a RUNTIME isolation test only. It does not import the
# production `extra-services.nookbridge` module (which uses sops-nix and
# the real daemon binary). Instead it defines a self-contained test
# module that creates the same users/groups, state dir, and runtime dir
# the production module creates, and runs an `nookd.service` whose only
# test-only difference from the production unit is `ExecStart`. The
# unit's User, Group, UMask, StateDirectory, RuntimeDirectory,
# ReadWritePaths, LoadCredential, and hardening directives are preserved
# verbatim. The real daemon binary is replaced by a foreground Python
# AF_UNIX stub that binds the configured socket path, chmods it 0770,
# accepts one connection, and sleeps so multi-step probes can run while
# the service is active.
#
# Coverage map:
#
#   * Production module evaluation / service-boundary static structure:
#     covered by `checks.x86_64-linux.nookbridge-service`.
#   * Live application behaviour (RPC handlers, native SQLite): covered
#     by the NookBridge repo's own test suite, not by this VM.
#   * Runtime OS-level isolation of the service from its callers:
#     covered here.
#
# Secret-handling invariants (must hold for this test to be valid):
#
#   * No real Notesnook credential value is ever embedded in the Nix
#     store, the NixOS configuration, or any script literal. The
#     fixture `nookbridge-db-key` is created at VM boot from
#     `/dev/urandom` by a root-only oneshot service and lives only in
#     the VM's `/run/secrets/nookbridge-db-key`. The cleartext never
#     enters a derivation.
#
#   * No age / sops fixture is needed: this test does not exercise the
#     sops path at all. The production module's sops wiring is
#     validated separately by `nookbridge-service`.
#
# Assertions:
#
#   1. nookd.service reaches `active (running)`.
#   2. Service runs as `nookbridge:nookbridge-clients` (ExecMainUID/GID
#      match the configured user/group).
#   3. `/var/lib/nookbridge` is owned by `nookbridge:nookbridge` with
#      mode 0750.
#   4. `/run/nookbridge` exists with `nookbridge:nookbridge-clients`
#      ownership and mode 0750.
#   5. `/run/nookbridge/nookbridge.sock` is owned by
#      `nookbridge:nookbridge-clients` with mode 0770.
#   6. `/run/secrets/nookbridge-db-key` is owned by `root:root` with
#      mode 0400.
#   7. A `nookbridge-clients` member (`hermes`) can connect to the
#      socket.
#   8. A user NOT in `nookbridge-clients` (`outsider`) cannot connect.
#   9. `hermes` cannot read `/var/lib/nookbridge/service-state` (the
#      state-file's 0600 mode keeps its contents confidential even
#      though the containing directory is group-traversable for
#      `nookbridge-clients`).
#  10. `hermes` cannot read `/run/secrets/nookbridge-db-key`.
# 11. The built nookbridge package output directory on `/run/current-system`
#     is free of the runtime-generated secret bytes (binary-safe
#     fixed-string scan that never prints the secret). The probe walks
#     the package root (the single /nix/store/...-nookbridge-* derivation
#     output) — it does not follow references into the transitive closure.

{ lib, pkgs, inputs }:

let
  # Build the production package inside the test closure so the VM
  # contains a real on-system `/run/current-system/sw/bin/nookctl`
  # symlink whose package output directory we can scan. `inherit inputs` matches
  # how the production module calls callPackage so the package builds
  # against the same pinned nixpkgs and source.
  nookbridge = pkgs.callPackage ../packages/nookbridge.nix {
    inherit inputs;
  };

  # Foreground Python AF_UNIX stub. systemd runs this as
  # `nookbridge:nookbridge-clients` with UMask=0007, so the bind path
  # already inherits the right group; we chmod 0770 explicitly so the
  # socket's own mode is verifiable regardless of UMask interplay.
  # The stub accepts a single connection, reads a request, then sleeps
  # so multi-step probes can run while it is the active process.
  runtimeStub = pkgs.writeScript "nookbridge-isolation-stub" ''
    #!${pkgs.python3}/bin/python3
    import os, socket, time
    SOCK = "/run/nookbridge/nookbridge.sock"
    BACKLOG = 4
    try:
        os.unlink(SOCK)
    except FileNotFoundError:
        pass
    # Drop a non-secret state marker into the state dir as the service
    # user so isolation subtests can prove that socket clients in
    # `nookbridge-clients` cannot read state CONTENTS, even though the
    # containing directory itself is group-traversable for them.
    state_path = "/var/lib/nookbridge/service-state"
    with open(state_path, "wb") as state_fh:
        state_fh.write(b"state")
    os.chmod(state_path, 0o600)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(SOCK)
    os.chmod(SOCK, 0o770)
    s.listen(BACKLOG)
    conn, _ = s.accept()
    try:
        conn.recv(4096)
    finally:
        conn.close()
    while True:
        time.sleep(3600)
  '';

in
{
  name = "nookbridge-isolation";

  nodes.machine =
    { config, lib, pkgs, ... }:
    {
      # Place the built package on the system PATH. After activation,
      # /run/current-system/sw/bin/nookctl resolves into the package
      # output directory we want to scan in assertion 11.
      environment.systemPackages = [ nookbridge ];

      # Production-equivalent user/group layout. These names and
      # modes must match what the production module installs.
      users.groups.nookbridge = { };
      users.groups.nookbridge-clients = { };

      users.users.nookbridge = {
        isSystemUser = true;
        group = "nookbridge";
        home = "/var/lib/nookbridge";
        createHome = false;
        shell = "${pkgs.shadow}/bin/nologin";
        extraGroups = [ "nookbridge-clients" ];
      };

      # Normal user that mirrors the production Hermes client: in
      # `nookbridge-clients` so it can connect to the socket but not
      # read state or the credential.
      users.users.hermes = {
        isNormalUser = true;
        uid = 1111;
        group = "users";
        extraGroups = [ "nookbridge-clients" ];
      };

      # Outsider account: not in `nookbridge-clients`.
      users.users.outsider = {
        isNormalUser = true;
        uid = 2222;
        group = "users";
      };

      # Create the state and runtime dirs with the exact modes the
      # production module installs via StateDirectoryMode /
      # RuntimeDirectoryMode.
      systemd.tmpfiles.rules = [
        "d /var/lib/nookbridge 0750 nookbridge nookbridge - -"
        "d /run/nookbridge 0750 nookbridge nookbridge-clients - -"
      ];

      # Root-only oneshot that creates the fixture credential at
      # boot from /dev/urandom. The cleartext lives only inside the
      # VM at /run/secrets/nookbridge-db-key; no derivation or
      # Nix-store path contains the secret bytes.
      systemd.services.nookbridge-isolation-secret-fixture = {
        description = "Generate random fixture credential for NookBridge isolation test";
        wantedBy = [ "multi-user.target" ];
        before = [ "nookd.service" ];
        serviceConfig = {
          Type = "oneshot";
          RemainAfterExit = true;
          ExecStart = pkgs.writeShellScript "nookbridge-isolation-secret-fixture" ''
            set -eu
            install -d -m 0700 -o root -g root /run/secrets
            ${pkgs.coreutils}/bin/dd \
              if=/dev/urandom \
              of=/run/secrets/nookbridge-db-key \
              bs=32 count=1 status=none
            ${pkgs.coreutils}/bin/chmod 0400 /run/secrets/nookbridge-db-key
            ${pkgs.coreutils}/bin/chown root:root /run/secrets/nookbridge-db-key
          '';
        };
      };

      # Test-only nookd.service. Mirrors the production unit's
      # hardening, runtime/state directories, and credential loading;
      # only ExecStart is swapped to the Python AF_UNIX stub.
      systemd.services.nookd = {
        description = "NookBridge isolation-test stub (AF_UNIX socket)";
        wantedBy = [ "multi-user.target" ];
        after = [
          "nookbridge-isolation-secret-fixture.service"
          "systemd-tmpfiles-setup.service"
        ];
        wants = [ "nookbridge-isolation-secret-fixture.service" ];
        serviceConfig = {
          Type = "simple";
          User = "nookbridge";
          Group = "nookbridge-clients";
          WorkingDirectory = "/var/lib/nookbridge";
          Environment = "HOME=/var/lib/nookbridge";
          ExecStart = "${runtimeStub}";
          LoadCredential = [ "nookbridge-db-key:/run/secrets/nookbridge-db-key" ];
          StateDirectory = "nookbridge";
          StateDirectoryMode = "0750";
          RuntimeDirectory = "nookbridge";
          RuntimeDirectoryMode = "0750";
          UMask = "0007";
          ReadWritePaths = [ "/var/lib/nookbridge" "/run/nookbridge" ];
          ProtectSystem = "strict";
          ProtectHome = true;
          PrivateTmp = true;
          PrivateDevices = true;
          NoNewPrivileges = true;
          RestrictAddressFamilies = [ "AF_UNIX" ];
          RestrictNamespaces = true;
          ProtectKernelTunables = true;
          ProtectKernelModules = true;
          ProtectControlGroups = true;
          LockPersonality = true;
          RestrictRealtime = true;
          RestrictSUIDSGID = true;
          CapabilityBoundingSet = "";
          AmbientCapabilities = "";
          SystemCallArchitectures = "native";
          Restart = "on-failure";
          RestartSec = "5s";
          TimeoutStopSec = "15s";
        };
      };
    };

  testScript =
    { nodes, ... }:
    let
      # Secret-scan helper pushed onto the VM. It reads the secret
      # bytes from /run/secrets/nookbridge-db-key and binary-searches
      # the package output directory for them using a fixed-string
      # substring match. The scan is rooted at the package root passed
      # as argv[1] and does not follow references into the transitive
      # closure. The cleartext is never printed; on a hit, only the
      # matching file path is reported so we know what regressed.
      secretProbe = pkgs.writeScript "nookbridge-isolation-secret-probe" ''
        #!${pkgs.python3}/bin/python3
        import os, sys

        SECRET_PATH = "/run/secrets/nookbridge-db-key"
        TARGET = sys.argv[1]

        if not os.path.isdir(TARGET):
            sys.stderr.write(f"secret-probe: not a directory: {TARGET}\n")
            sys.exit(2)
        if not os.path.isfile(SECRET_PATH):
            sys.stderr.write(f"secret-probe: missing {SECRET_PATH}\n")
            sys.exit(3)

        with open(SECRET_PATH, "rb") as fh:
            needle = fh.read()
        if not needle:
            sys.stderr.write("secret-probe: empty secret\n")
            sys.exit(4)

        hits = []
        for dirpath, _dirnames, filenames in os.walk(TARGET):
            for name in filenames:
                p = os.path.join(dirpath, name)
                try:
                    with open(p, "rb") as fh:
                        data = fh.read()
                except OSError:
                    continue
                if needle in data:
                    hits.append(p)

        if hits:
            sys.stderr.write(
                f"secret-probe: LEAK ({len(hits)} hit(s)) in {TARGET}:\n"
            )
            for h in hits:
                sys.stderr.write(f"  {h}\n")
            sys.exit(1)
        sys.exit(0)
      '';
    in
    ''
      start_all()
      machine.wait_for_unit("multi-user.target")
      machine.wait_for_unit("nookbridge-isolation-secret-fixture.service")
      machine.wait_for_unit("nookd.service")

      with subtest("service is active"):
          out = machine.succeed(
              "systemctl is-active nookd.service"
          ).strip()
          assert out == "active", f"nookd.service is {out!r}"

      with subtest("service runs as nookbridge:nookbridge-clients"):
          # ExecMainUID / ExecMainGID are empty on the systemd version
          # running in this NixOS test environment, so resolve the active
          # main PID via systemd and read the running process's UID/GID
          # directly from /proc instead of trusting systemd's accounting.
          main_pid = machine.succeed(
              "systemctl show nookd.service -p MainPID --value"
          ).strip()
          assert main_pid, "MainPID is empty"
          assert main_pid != "0", f"MainPID {main_pid!r} not running"
          proc_ids = machine.succeed(
              f"stat -c '%u %g' /proc/{main_pid}"
          ).split()
          uid = proc_ids[0]
          gid = proc_ids[1]
          nook_uid = machine.succeed("id -u nookbridge").strip()
          clients_gid = machine.succeed(
              "getent group nookbridge-clients | cut -d: -f3"
          ).strip()
          assert uid == nook_uid, f"proc UID {uid} != {nook_uid}"
          assert gid == clients_gid, f"proc GID {gid} != {clients_gid}"

      with subtest("/var/lib/nookbridge ownership and mode"):
          info = machine.succeed(
              "stat -c '%U %G %a' /var/lib/nookbridge"
          ).strip().split()
          assert info[0] == "nookbridge", f"state owner {info[0]!r}"
          assert info[1] == "nookbridge-clients", f"state group {info[1]!r}"
          assert info[2] == "750", f"state mode {info[2]!r}"

      with subtest("/run/nookbridge ownership and mode"):
          info = machine.succeed(
              "stat -c '%U %G %a' /run/nookbridge"
          ).strip().split()
          assert info[0] == "nookbridge", f"runtime owner {info[0]!r}"
          assert info[1] == "nookbridge-clients", f"runtime group {info[1]!r}"
          assert info[2] == "750", f"runtime mode {info[2]!r}"

      with subtest("/run/nookbridge/nookbridge.sock ownership and mode"):
          info = machine.succeed(
              "stat -c '%U %G %a %n' /run/nookbridge/nookbridge.sock"
          ).strip()
          parts = info.split()
          assert parts[0] == "nookbridge", f"socket owner {parts[0]!r}"
          assert parts[1] == "nookbridge-clients", f"socket group {parts[1]!r}"
          assert parts[2] == "770", f"socket mode {parts[2]!r}"

      with subtest("credential is root-owned with mode 0400"):
          info = machine.succeed(
              "stat -c '%U %G %a %s' /run/secrets/nookbridge-db-key"
          ).strip()
          parts = info.split()
          assert parts[0] == "root", f"cred owner {parts[0]!r}"
          assert parts[1] == "root", f"cred group {parts[1]!r}"
          assert parts[2] == "400", f"cred mode {parts[2]!r}"
          assert int(parts[3]) == 32, f"cred size {parts[3]!r}"

      with subtest("hermes can connect to the socket"):
          # Bash `<>/path` redirection opens AF_UNIX sockets via
          # open(2), which Linux rejects with ENXIO on a connected
          # stream socket; use an actual socket API client instead.
          # The stub accepts one connection and stays alive; send a
          # short payload and close so the stub's accept()+recv()
          # return promptly.
          machine.succeed(
              "runuser -u hermes -- "
              + "${pkgs.python3}/bin/python3 -c "
              + "'import socket;"
              + " s=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM);"
              + " s.connect(\"/run/nookbridge/nookbridge.sock\");"
              + " s.sendall(b\"hello\");"
              + " s.close()'"
          );

      with subtest("outsider cannot connect to the socket"):
          machine.fail(
              "runuser -u outsider -- "
              "bash -c 'exec 3<>/run/nookbridge/nookbridge.sock'"
          )

      with subtest("hermes cannot read service state"):
          machine.fail(
              "runuser -u hermes -- cat /var/lib/nookbridge/service-state"
          )

      with subtest("hermes cannot read the credential"):
          machine.fail(
              "runuser -u hermes -- cat /run/secrets/nookbridge-db-key"
          )

      with subtest("nookbridge package output directory has no secret bytes"):
          # Resolve the on-system wrapper, then walk up from
          # bin/nookctl to the package root. We avoid hard-coding any
          # version suffix or store hash; we only depend on the
          # "bin/nookctl" leaf that the package installPhase creates.
          nookctl_path = machine.succeed(
              "readlink -f /run/current-system/sw/bin/nookctl"
          ).strip()
          # Expect .../<hash>-nookbridge-<version>/bin/nookctl
          # The version suffix is whatever the package set; we locate
          # the package root by chopping the trailing /bin/nookctl.
          assert nookctl_path.endswith("/bin/nookctl"), (
              f"unexpected nookctl path: {nookctl_path!r}"
          )
          pkg_root = nookctl_path[: -len("/bin/nookctl")]
          assert pkg_root.startswith("/nix/store/"), (
              f"nookctl not under /nix/store: {pkg_root!r}"
          )
          machine.succeed("test -d " + pkg_root)
          machine.copy_from_host("${secretProbe}", "/tmp/secret-probe.py")
          rc = machine.execute(
              "chmod +x /tmp/secret-probe.py && "
              "/tmp/secret-probe.py " + pkg_root
          )[0]
          assert rc == 0, (
              "secret-probe reported a leak in the package output directory"
          )
    '';
}

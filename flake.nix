# ==================================================================================================
# assistant-bench Development Flake
# ==================================================================================================

{
  description = "assistant-bench development environment";

  # ------------------------------------------------------------------------------------------------
  # Inputs
  # ------------------------------------------------------------------------------------------------
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

    flake-utils.url = "github:numtide/flake-utils";

    git-hooks-nix = {
      url = "github:cachix/git-hooks.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Only pure data and prompt files are imported, so the input skips nixos-config's own inputs.
    nixos-config = {
      url = "github:hakula139/nixos-config";
      flake = false;
    };
  };

  # ------------------------------------------------------------------------------------------------
  # Outputs
  # ------------------------------------------------------------------------------------------------
  outputs =
    {
      nixpkgs,
      flake-utils,
      git-hooks-nix,
      nixos-config,
      ...
    }:
    let
      # git-hooks.nix has dropped x86_64-darwin, which Nixpkgs supports only through 26.05.
      systems = [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-linux"
      ];
    in
    flake-utils.lib.eachSystem systems (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        # ------------------------------------------------------------------------------------------
        # nixos-config Export
        # ------------------------------------------------------------------------------------------
        promptFragments = "${nixos-config}/home/modules/llm-assistants/shared/prompt-fragments";
        nixosConfigExport = pkgs.writeText "nixos-config.json" (
          builtins.toJSON {
            inherit (nixos-config) rev;
            catalog = import "${nixos-config}/data/llm-models.nix";
            instructions = import "${nixos-config}/home/modules/llm-assistants/instructions" {
              inherit (import promptFragments) readPrompt;
            };
          }
        );

        # ------------------------------------------------------------------------------------------
        # Python Environment
        # ------------------------------------------------------------------------------------------
        pythonEnvironment = {
          packages = [ pkgs.uv ] ++ nodeTools;
          env.BENCH_NIXOS_CONFIG = nixosConfigExport;
        };

        # ------------------------------------------------------------------------------------------
        # Pre-commit Hooks
        # ------------------------------------------------------------------------------------------
        hooks = {
          end-of-file-fixer.enable = true;
          trim-trailing-whitespace.enable = true;
          check-added-large-files.enable = true;
          check-yaml.enable = true;
          taplo.enable = true;
          actionlint = {
            enable = true;
            package = pkgs.actionlint.overrideAttrs (old: {
              patches = (old.patches or [ ]) ++ [
                (pkgs.fetchpatch {
                  url = "https://github.com/rhysd/actionlint/commit/644076a59742c2d1540ebd4686eab3c308f0e562.patch";
                  hash = "sha256-Lpd7CTkLbzgvAZV2H8G4GTUoPNDCK7PiW3A40+cquUk=";
                })
              ];
            });
          };

          lychee.enable = true;

          sort-file-contents = {
            enable = true;
            files = "^\\.cspell/words\\.txt$";
            settings.ignore-case = true;
          };

          nixfmt.enable = true;
          deadnix.enable = true;
          statix.enable = true;

          # `shfmt` reads `.editorconfig` only when the entry passes no formatting flag. Any flag
          # reverts it to tabs and ignores the `binary_next_line` and `switch_case_indent` keys.
          shfmt = {
            enable = true;
            settings = {
              language-dialect = null;
              simplify = false;
              indent = null;
              binary-next-line = false;
              case-indent = false;
            };
          };
        };

        nodeTools = with pkgs; [
          nodejs_26
          (pnpm.override { nodejs-slim = nodejs-slim_26; })
        ];

        # These hooks run the tools pinned in node_modules and uv.lock, which the Nix sandbox lacks.
        # The flake check leaves them to CI's Node Check and Python Check jobs.
        nodeHook =
          name: cmd:
          pkgs.lib.getExe (
            pkgs.writeShellApplication {
              inherit name;
              runtimeInputs = nodeTools;

              text = ''
                if [ ! -d node_modules ]; then
                  echo "node_modules is missing. Run pnpm install, then commit again." >&2
                  exit 1
                fi
                pnpm exec ${cmd} "$@"
              '';
            }
          );

        nodeHooks = {
          prettier = {
            enable = true;
            package = null;
            entry = nodeHook "prettier" "prettier --write --ignore-unknown";
          };

          cspell = {
            enable = true;
            package = null;
            entry = nodeHook "cspell" "cspell --no-must-find-files --no-progress";
            types = [ "text" ];
          };

          markdownlint = {
            enable = true;
            name = "markdownlint-cli2";
            package = null;
            entry = nodeHook "markdownlint" "markdownlint-cli2 --fix";
            files = "\\.md$";
          };
        };

        uvExe = pkgs.lib.getExe pkgs.uv;

        uvHooks = {
          ruff = {
            enable = true;
            package = null;
            entry = "${uvExe} run --locked ruff check --fix";
            types = [ "file" ];
            types_or = [
              "python"
              "pyi"
            ];
          };

          ruff-format = {
            enable = true;
            package = null;
            entry = "${uvExe} run --locked ruff format";
            types = [ "file" ];
            types_or = [
              "python"
              "pyi"
            ];
          };
        };

        preCommit = git-hooks-nix.lib.${system}.run {
          src = ./.;
          hooks = hooks // nodeHooks // uvHooks;
        };

        # lychee builds its HTTP client even offline and fails without CA roots, which the Nix
        # sandbox lacks until cacert's setup hook exports SSL_CERT_FILE. The dev shell keeps the
        # host's CA settings, so only the check gets cacert.
        preCommitCheck = git-hooks-nix.lib.${system}.run {
          src = ./.;
          hooks = hooks // {
            lychee = hooks.lychee // {
              extraPackages = [ pkgs.cacert ];
            };
          };
        };

        cmp = pkgs.lib.getExe' pkgs.diffutils "cmp";
        flock = pkgs.lib.getExe pkgs.flock;
        git = pkgs.lib.getExe pkgs.git;
      in
      {
        # ------------------------------------------------------------------------------------------
        # Dev Shells
        # ------------------------------------------------------------------------------------------
        devShells.python = pkgs.mkShell (pythonEnvironment // { name = "assistant-bench-python"; });

        devShells.default = pkgs.mkShell {
          name = "assistant-bench-dev";
          inherit (pythonEnvironment) env;

          packages = pythonEnvironment.packages ++ preCommit.enabledPackages ++ [ preCommit.config.package ];

          # Other repositories enter this shell for its tools, and the flake's source is a store
          # copy that can't name its checkout, so hooks install only where the repository's
          # top-level flake.nix matches this file.
          #
          # git-hooks.nix stores a hooks path relative to the main checkout, which linked
          # worktrees can't resolve (cachix/git-hooks.nix#688), so
          # unset it to fall back to the shared hooks directory. The lock keeps a concurrent load
          # from setting it while `pre-commit install` runs, since pre-commit refuses to install
          # with the path set.
          #
          # The hooks' Python packages also fill PYTHONPATH, which would shadow the project's
          # virtualenv, so unset it.
          shellHook = ''
            unset PYTHONPATH
            if toplevel=$(${git} rev-parse --show-toplevel 2>/dev/null) \
              && ${cmp} -s ${./flake.nix} "$toplevel/flake.nix"; then
              common=$(${git} rev-parse --path-format=absolute --git-common-dir)
              (
                ${flock} 9
                ${preCommit.config.installationScript}
                ${git} config --local --unset-all core.hooksPath || true
              ) 9>"$common/git-hooks.lock"
            fi
          '';
        };

        # ------------------------------------------------------------------------------------------
        # Packages
        # ------------------------------------------------------------------------------------------
        packages.nixos-config = nixosConfigExport;

        # ------------------------------------------------------------------------------------------
        # Checks
        # ------------------------------------------------------------------------------------------
        checks.pre-commit = preCommitCheck;

        # ------------------------------------------------------------------------------------------
        # Formatter
        # ------------------------------------------------------------------------------------------
        formatter = pkgs.nixfmt-tree;
      }
    );
}

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

    devkit = {
      url = "github:hakula139/devkit/v1";
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
      devkit,
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
        dk = devkit.lib.mkLib pkgs;

        # ------------------------------------------------------------------------------------------
        # nixos-config Export
        # ------------------------------------------------------------------------------------------
        promptFragments = import "${nixos-config}/home/modules/llm-assistants/shared/prompt-fragments";
        nixosConfigExport = pkgs.writeText "nixos-config.json" (
          builtins.toJSON {
            inherit (nixos-config) rev;
            catalog = import "${nixos-config}/data/llm-models.nix";
            instructions = import "${nixos-config}/home/modules/llm-assistants/instructions" {
              inherit promptFragments;
            };
          }
        );

        # ------------------------------------------------------------------------------------------
        # Node Toolchain
        # ------------------------------------------------------------------------------------------
        nodeMajor = "26";
        nodejs = pkgs."nodejs_${nodeMajor}";
        pnpm = pkgs.pnpm.override { nodejs-slim = pkgs."nodejs-slim_${nodeMajor}"; };
        nodeTools = [
          nodejs
          pnpm
        ];

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
          actionlint.enable = true;
          check-yaml.enable = true;
          cspell.enable = true;
          deadnix.enable = true;
          dprint.enable = false;
          editorconfig-checker.enable = true;
          markdownlint-cli2.enable = true;
          nixfmt.enable = true;
          shellcheck.enable = true;
          shfmt.enable = true;
          sort-file-contents.enable = true;
          statix.enable = true;
          taplo.enable = true;

          lychee = {
            enable = true;
            extraPackages = [ pkgs.cacert ];
          };
        };

        uvHooks = {
          ruff = {
            enable = true;
            package = null;
            entry = "${pkgs.lib.getExe (dk.uvTool "ruff")} check --fix";
            types = [ "file" ];
            types_or = [
              "python"
              "pyi"
            ];
          };

          ruff-format = {
            enable = true;
            package = null;
            entry = "${pkgs.lib.getExe (dk.uvTool "ruff")} format";
            types = [ "file" ];
            types_or = [
              "python"
              "pyi"
            ];
          };
        };

        preCommit = dk.preCommit {
          src = ./.;
          inherit hooks;
          devHooks = uvHooks // {
            prettier = {
              enable = true;
              package = null;
              entry = "${pkgs.lib.getExe (dk.nodeTool "prettier")} --write --ignore-unknown";
              files = "\\.(astro|css|js|json|jsonc|md|mdx|ts|tsx|ya?ml)$";
            };
          };
        };

        # ------------------------------------------------------------------------------------------
        # GitLab Pages Toolchain
        # ------------------------------------------------------------------------------------------
        pagesVariable = name: ".pages.variables.${name} // .variables.${name}";
        pagesCheck =
          name: label: query: pattern: expected:
          pkgs.runCommand name { nativeBuildInputs = [ pkgs.yq-go ]; } ''
            value=$(yq '${query}' ${./.gitlab/ci/pages.yml})
            if [[ "$value" != ${pattern} ]]; then
              echo "${label} in .gitlab/ci/pages.yml is $value, but the dev shell has" \
                "${expected}." >&2
              exit 1
            fi
            touch "$out"
          '';
      in
      {
        # ------------------------------------------------------------------------------------------
        # Dev Shells
        # ------------------------------------------------------------------------------------------
        devShells.python = pkgs.mkShell (pythonEnvironment // { name = "assistant-bench-python"; });

        devShells.default = pkgs.mkShell {
          name = "assistant-bench-dev";
          inherit (pythonEnvironment) env;

          inherit (pythonEnvironment) packages;
          inputsFrom = [ preCommit.shell ];
        };

        # ------------------------------------------------------------------------------------------
        # Packages
        # ------------------------------------------------------------------------------------------
        packages.nixos-config = nixosConfigExport;

        # ------------------------------------------------------------------------------------------
        # Checks
        # ------------------------------------------------------------------------------------------
        checks = {
          pre-commit = preCommit.check;
          pages-pnpm =
            pagesCheck "pages-pnpm" "BENCH_PNPM_VERSION" (pagesVariable "BENCH_PNPM_VERSION") pnpm.version
              "pnpm ${pnpm.version}";
          pages-node =
            pagesCheck "pages-node" "The pages job's image"
              "(${pagesVariable "BENCH_NODE_IMAGE"}) as $v | (.pages.image // .image) | sub(\"\\$BENCH_NODE_IMAGE\"; $v)"
              "node:${nodeMajor}-*"
              "Node.js ${nodeMajor}";
        };

        # ------------------------------------------------------------------------------------------
        # Formatter
        # ------------------------------------------------------------------------------------------
        formatter = pkgs.nixfmt-tree;
      }
    );
}

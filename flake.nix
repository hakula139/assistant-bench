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
      devkit,
      nixos-config,
      ...
    }:
    let
      systems = [
        "aarch64-darwin"
        "x86_64-linux"
      ];
    in
    devkit.lib.eachSystem systems (
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
              devkitSource = devkit;
            };
          }
        );

        # ------------------------------------------------------------------------------------------
        # Node Toolchain
        # ------------------------------------------------------------------------------------------
        nodeMajor = "26";
        nodejs = pkgs.nodejs_26;
        pnpm = pkgs.pnpm_12;
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
          dprint.enable = true;
          editorconfig-checker.enable = true;
          lychee.enable = true;
          markdownlint-cli2.enable = true;
          nixfmt.enable = true;
          shellcheck.enable = true;
          shfmt.enable = true;
          sort-file-contents.enable = true;
          statix.enable = true;
          taplo.enable = true;

          ruff = {
            enable = true;
            types = [ "file" ];
            types_or = [
              "python"
              "pyi"
            ];
          };

          ruff-format = {
            enable = true;
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
          devHooks = {
            prettier = {
              enable = true;
              package = dk.nodeTool "prettier";
              files = "\\.(astro|css|js|json|jsonc|ts|tsx|ya?ml)$";
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

        devShells.default = pkgs.mkShell (
          pythonEnvironment
          // {
            name = "assistant-bench-dev";
            inputsFrom = [ preCommit.shell ];
          }
        );

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

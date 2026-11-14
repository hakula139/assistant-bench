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
      url = "github:hakula139/devkit/v3";
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
    devkit.lib.eachSystem devkit.lib.systems (
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
        # Pre-commit Hooks
        # ------------------------------------------------------------------------------------------
        preCommit = dk.preCommit {
          src = ./.;
          hooks = {
            lychee.enable = true;
            mypy.enable = true;
            ruff.enable = true;
            ruff-format.enable = true;
            shellcheck.enable = true;
            shfmt.enable = true;
            taplo.enable = true;

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
        inherit (dk.nodeToolchain) pnpm;
        nodeMajor = pkgs.lib.versions.major dk.nodeToolchain.nodejs.version;
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
        # Dev Shell
        # ------------------------------------------------------------------------------------------
        devShells.default = pkgs.mkShell {
          name = "assistant-bench-dev";
          packages = builtins.attrValues dk.nodeToolchain ++ builtins.attrValues dk.uvToolchain;
          inputsFrom = [ preCommit ];
          env.BENCH_NIXOS_CONFIG = nixosConfigExport;
        };

        # ------------------------------------------------------------------------------------------
        # Packages
        # ------------------------------------------------------------------------------------------
        packages.nixos-config = nixosConfigExport;

        # ------------------------------------------------------------------------------------------
        # Checks
        # ------------------------------------------------------------------------------------------
        checks = {
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

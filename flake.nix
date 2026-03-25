{
  description = "Email automation platform with LLM processing, rules engine, and integrations";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
      in
      {
        packages = {
          emma = pkgs.callPackage ./nix/package.nix { };
          default = self.packages.${system}.emma;
        };

        devShells = import ./nix/shell.nix { inherit pkgs self system; };
      }
    ) // {
      # System-independent outputs
      homeManagerModules = {
        emma = import ./nix/home.nix;
        default = self.homeManagerModules.emma;
      };

      overlays.default = final: prev: {
        emma = final.callPackage ./nix/package.nix { };
      };
    };
}

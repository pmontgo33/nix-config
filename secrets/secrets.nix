let

  plasma-vm-nixos_patrick = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDXXXLRz5H2YLUGkvx6KTd5h/xTv1luy/51YPkank4LG";
  hp-nixos_patrick = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEr9aBBJ73I/tXOT00krxHglmAqZ0A8xt7Hk5s2zMwCo";
  users = [ plasma-vm-nixos_patrick hp-nixos_patrick ];

  plasma-vm-nixos_system = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHvLDQ46pJQTzxM9/nU6GMO7EsB9LCdZEELl4YY0F/4Y";
  hp-nixos_system = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMZIPGwINVdrqVoIzupSTMJFOty431KipytXKaKRFdHT";
  nix-fury_system = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILvna6/m8kyTOf78WA680y4z+wzJ2NjNwnNjnC78GSCf";
  systems = [ plasma-vm-nixos_system hp-nixos_system nix-fury_system ];

in
{
  "tailscale_auth_key.age".publicKeys = users ++ systems;
  # "secret1.age".publicKeys = [ users systems ];
}

let
  # user1 = "ssh-ed25519 ";
  # users = [ user1 ];

  #TODO Add users and systems. Figure out how to make the lists work.

  plasma-vm-nixos = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDXXXLRz5H2YLUGkvx6KTd5h/xTv1luy/51YPkank4LG";
  hp-nixos_patrick = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEr9aBBJ73I/tXOT00krxHglmAqZ0A8xt7Hk5s2zMwCo";
  hp-nixos_system = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMZIPGwINVdrqVoIzupSTMJFOty431KipytXKaKRFdHT";
  keys = [ plasma-vm-nixos hp-nixos_patrick hp-nixos_system ];
in
{
  "tailscale_auth_key.age".publicKeys = [ plasma-vm-nixos hp-nixos_patrick hp-nixos_system ];
  # "secret1.age".publicKeys = [ users systems ];
}

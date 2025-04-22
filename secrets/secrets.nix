let
  # user1 = "ssh-ed25519 ";
  # users = [ user1 ];

  plasma-vm-nixos = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDXXXLRz5H2YLUGkvx6KTd5h/xTv1luy/51YPkank4LG";
  hp-nixos = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEr9aBBJ73I/tXOT00krxHglmAqZ0A8xt7Hk5s2zMwCo";
  keys = [ plasma-vm-nixos hp-nixos ];
in
{
  "secret1.age".publicKeys = [ keys ];
  # "secret1.age".publicKeys = [ users systems ];
}

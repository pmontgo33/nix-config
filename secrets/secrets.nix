let
  # user1 = "ssh-ed25519 ";
  # users = [ user1 ];

  plasma-vm-nixos = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDXXXLRz5H2YLUGkvx6KTd5h/xTv1luy/51YPkank4LG";
  systems = [ plasma-vm-nixos ];
in
{
  "secret1.age".publicKeys = [ plasma-vm-nixos ];
  # "secret1.age".publicKeys = [ users systems ];
}

// Compiled only into the synthetic CI harness, never into a shipping installer.
procedure Expect(Condition: Boolean; Name: String);
begin
  if not Condition then RaiseException('Space test failed: ' + Name);
end;

procedure SpaceSelfTests;
var R: TSpaceRoles; D, P, Existing, Identity, Target: String; I: Integer;
begin
  for I := 0 to 2 do begin
    R[I].Name := 'Role'; R[I].Path := 'Fixture'; R[I].Known := True;
    R[I].Needed := 100; R[I].Free := 300; R[I].Volume := 'same';
  end;
  Expect(Assess(R, D, P), 'shared exact boundary');
  R[2].Free := 299;
  Expect(not Assess(R, D, P), 'shared roles sum and lowest free snapshot');
  R[0].Volume := 'other'; R[0].Free := 100; R[1].Free := 200; R[2].Free := 200;
  Expect(Assess(R, D, P), 'separate installation and shared temporary/data volumes');
  R[2].Free := 199;
  Expect(not Assess(R, D, P), 'moving install cannot cure low temporary/data space');
  R[1].Known := False;
  Expect(not Assess(R, D, P), 'unknown space fails closed');
  for I := 0 to 2 do begin
    R[I].Known := True; R[I].Needed := StrToInt64('4000000000');
    R[I].Free := StrToInt64('12000000000'); R[I].Volume := 'same';
  end;
  Expect(Assess(R, D, P), '64-bit amounts beyond 4 GiB');
  R[2].Free := StrToInt64('11999999999');
  Expect(not Assess(R, D, P), '64-bit one byte shortage');
  Target := ExpandConstant('{tmp}') + '\does-not-exist\HomeServer';
  Expect(IdentifyVolume(Target, Identity, Existing), 'resolve existing parent without creating folders');
  Expect(not DirExists(Target), 'read-only disk inspection');
  ReadRole(R[0], 'real volume', ExpandConstant('{tmp}'), 1);
  Expect(R[0].Known and (R[0].Free > 0), 'real Windows free-space API');
  Expect(not IdentifyVolume('Z:\unavailable-fixture\HomeServer', Identity, Existing), 'unavailable drive');
end;

function InitializeSetup: Boolean;
var Report: String;
begin
  Result := True;
  Report := ExpandConstant('{param:SPACESELFTEST|}');
  if Report <> '' then begin
    SpaceSelfTests;
    if not SaveStringToFile(Report, 'PASS native shared/separate-volume, low/unknown, 64-bit, existing-parent and Windows API checks', False) then
      RaiseException('Could not write the self-test report');
    Result := False;
  end;
end;

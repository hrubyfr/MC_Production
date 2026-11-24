void check_wcsim(const char *filename, int expected, const char *tree_name = "wcsimT")
{
    TFile file(filename, "READ");
    TTree *tree = nullptr;
    if (!file.IsZombie() && !file.TestBit(TFile::kRecovered))
        file.GetObject(tree_name, tree);
    gSystem->Exit(tree && tree->GetEntries() == expected ? 0 : 1);
}
// Per-event diagnostics for a file produced by MergeOutputFiles.cc.
// Usage: root -l -b -q 'CheckMergedOutput.cc("merged.root", 50)'   (maxPrint: -1 = all events)
#include <iomanip>
#include <iostream>
#include <map>
#include <set>
#include <string>
#include <vector>

#include "TBranch.h"
#include "TFile.h"
#include "TObjArray.h"
#include "TTree.h"

R__LOAD_LIBRARY(/opt/WCSim/build/install/lib/libWCSimRoot.so)

namespace {
struct EventRow {
    Long64_t entry;
    int evtNum;
    int nSubEvents;
    int nTracks;
    int nCherHits;
    int nDigiHits;
    int trigType;
    double sumQ;
    double vtx[3];
    Long64_t bytes;
};
}

int CheckMergedOutput(const char *filename, Long64_t maxPrint = -1)
{
    TFile *file = TFile::Open(filename, "READ");
    if (!file || file->IsZombie()) {
        std::cerr << "Error: cannot open " << filename << std::endl;
        return 1;
    }

    int status = 0;

    // Metadata trees
    for (const char *name : {"wcsimT", "wcsimGeoT", "wcsimRootOptionsT", "AllSecondaries"}) {
        TTree *tree = nullptr;
        file->GetObject(name, tree);
        if (tree)
            std::cout << std::left << std::setw(20) << name << " entries = " << tree->GetEntries() << std::endl;
        else {
            std::cout << std::left << std::setw(20) << name << " MISSING" << (std::string(name) == "AllSecondaries" ? " (optional)" : "") << std::endl;
            if (std::string(name) != "AllSecondaries") status = 1;
        }
    }

    TTree *events = nullptr;
    file->GetObject("wcsimT", events);
    if (!events) {
        file->Close();
        return 1;
    }

    WCSimRootEvent *superEvent = new WCSimRootEvent();
    events->SetBranchAddress("wcsimrootevent", &superEvent);
    const Long64_t nEntries = events->GetEntries();

    // Per-branch compressed/total sizes on disk
    std::cout << "\nwcsimT branches:\n"
              << std::left << std::setw(28) << "  branch" << std::right << std::setw(14) << "total [kB]"
              << std::setw(14) << "zipped [kB]" << std::endl;
    TObjArray *branches = events->GetListOfBranches();
    for (int i = 0; i < branches->GetEntries(); ++i) {
        TBranch *b = (TBranch *)branches->At(i);
        std::cout << "  " << std::left << std::setw(26) << b->GetName() << std::right << std::fixed << std::setprecision(1)
                  << std::setw(14) << b->GetTotalSize("*") / 1024. << std::setw(14) << b->GetZipBytes("*") / 1024. << std::endl;
    }

    std::vector<EventRow> rows;
    rows.reserve(nEntries);
    std::map<int, int> idCount;
    std::set<int> seenIds;
    int nDuplicates = 0, nEmpty = 0, nReadErrors = 0;
    Long64_t totalBytes = 0;

    for (Long64_t i = 0; i < nEntries; ++i) {
        const Int_t nb = events->GetEntry(i);
        if (nb <= 0) {
            ++nReadErrors;
            continue;
        }
        totalBytes += nb;

        EventRow r{};
        r.entry = i;
        r.bytes = nb;
        r.nSubEvents = superEvent->GetNumberOfEvents();
        r.evtNum = -1;
        r.trigType = -1;
        for (int k = 0; k < r.nSubEvents; ++k) {
            WCSimRootTrigger *trig = superEvent->GetTrigger(k);
            if (!trig) continue;
            if (k == 0) {
                r.evtNum = trig->GetHeader()->GetEvtNum();
                r.trigType = trig->GetTriggerType();
                for (int j = 0; j < 3; ++j) r.vtx[j] = trig->GetVtx(j);
            }
            r.nTracks += trig->GetNtrack();
            r.nCherHits += trig->GetNcherenkovhits();
            r.nDigiHits += trig->GetNcherenkovdigihits();
            TClonesArray *digis = trig->GetCherenkovDigiHits();
            for (int j = 0; digis && j < trig->GetNcherenkovdigihits(); ++j) {
                auto *d = (WCSimRootCherenkovDigiHit *)digis->At(j);
                if (d) r.sumQ += d->GetQ();
            }
        }
        if (r.nDigiHits == 0 && r.nCherHits == 0) ++nEmpty;
        if (!seenIds.insert(r.evtNum).second) ++nDuplicates;
        ++idCount[r.evtNum];
        rows.push_back(r);
    }

    std::cout << "\nPer-event summary (wcsimT):\n"
              << std::right << std::setw(8) << "entry" << std::setw(8) << "evtID" << std::setw(6) << "nSub" << std::setw(9) << "nTracks"
              << std::setw(10) << "nCherHit" << std::setw(10) << "nDigiHit" << std::setw(10) << "sumQ" << std::setw(8) << "trig"
              << std::setw(24) << "vtx [cm]" << std::setw(12) << "bytes" << std::endl;
    std::cout << std::string(105, '-') << std::endl;

    Long64_t printed = 0;
    for (const EventRow &r : rows) {
        if (maxPrint >= 0 && printed++ >= maxPrint) {
            std::cout << "  ... (" << rows.size() - maxPrint << " more events not printed)" << std::endl;
            break;
        }
        char vtx[64];
        snprintf(vtx, sizeof(vtx), "(%.0f,%.0f,%.0f)", r.vtx[0], r.vtx[1], r.vtx[2]);
        std::cout << std::right << std::setw(8) << r.entry << std::setw(8) << r.evtNum << std::setw(6) << r.nSubEvents << std::setw(9) << r.nTracks
                  << std::setw(10) << r.nCherHits << std::setw(10) << r.nDigiHits << std::fixed << std::setprecision(1) << std::setw(10) << r.sumQ
                  << std::setw(8) << r.trigType << std::setw(24) << vtx << std::setw(12) << r.bytes << std::endl;
    }

    // Overall statistics
    std::cout << "\nSummary:\n"
              << "  entries read          : " << rows.size() << " / " << nEntries << "\n"
              << "  read errors           : " << nReadErrors << "\n"
              << "  events w/o any hits   : " << nEmpty << "\n"
              << "  duplicate event IDs   : " << nDuplicates << "\n"
              << "  total bytes read      : " << totalBytes << std::endl;
    if (nDuplicates > 0) {
        std::cout << "  (IDs may legitimately repeat if merged files each restart numbering)\n";
    }
    if (nReadErrors > 0) status = 1;

    // AllSecondaries: generic per-branch entry sizes
    TTree *sec = nullptr;
    file->GetObject("AllSecondaries", sec);
    if (sec) {
        std::cout << "\nAllSecondaries branches (" << sec->GetEntries() << " entries):\n"
                  << std::left << std::setw(28) << "  branch" << std::right << std::setw(14) << "total [kB]"
                  << std::setw(14) << "zipped [kB]" << std::endl;
        TObjArray *sb = sec->GetListOfBranches();
        for (int i = 0; i < sb->GetEntries(); ++i) {
            TBranch *b = (TBranch *)sb->At(i);
            std::cout << "  " << std::left << std::setw(26) << b->GetName() << std::right << std::fixed << std::setprecision(1)
                      << std::setw(14) << b->GetTotalSize("*") / 1024. << std::setw(14) << b->GetZipBytes("*") / 1024. << std::endl;
        }

        // Each entry is one step: group consecutive entries with the same evt into one row.
        struct SecRow {
            Long64_t first, nSteps;
            int evt, nPrimary;
            std::set<int> tracks, pdgs;
            double edep, maxKE, maxTrkLen;
        };
        Int_t evt, trk, parent, pdg;
        Float_t ke, edep, trkLen;
        sec->SetBranchStatus("*", 0);
        const char *used[] = {"evt", "trk", "parent", "pdg", "ke_MeV", "edep_MeV", "track_length_cm"};
        for (const char *n : used) sec->SetBranchStatus(n, 1);
        sec->SetBranchAddress("evt", &evt);
        sec->SetBranchAddress("trk", &trk);
        sec->SetBranchAddress("parent", &parent);
        sec->SetBranchAddress("pdg", &pdg);
        sec->SetBranchAddress("ke_MeV", &ke);
        sec->SetBranchAddress("edep_MeV", &edep);
        sec->SetBranchAddress("track_length_cm", &trkLen);

        std::vector<SecRow> secRows;
        std::map<int, int> runsPerEvt; // number of separate runs of each evt
        int nBackwards = 0;            // evt decreased between consecutive steps (e.g. numbering restarted at a file boundary)
        const Long64_t nSec = sec->GetEntries();
        for (Long64_t i = 0; i < nSec; ++i) {
            if (sec->GetEntry(i) <= 0) continue;
            if (secRows.empty() || secRows.back().evt != evt) {
                if (!secRows.empty() && evt < secRows.back().evt) ++nBackwards;
                secRows.push_back({i, 0, evt, 0, {}, {}, 0., 0., 0.});
                ++runsPerEvt[evt];
            }
            SecRow &s = secRows.back();
            ++s.nSteps;
            if (s.tracks.insert(trk).second && parent == 0) ++s.nPrimary;
            s.pdgs.insert(pdg);
            s.edep += edep;
            if (ke > s.maxKE) s.maxKE = ke;
            if (trkLen > s.maxTrkLen) s.maxTrkLen = trkLen;
        }

        std::cout << "\nPer-event summary (AllSecondaries, entries = steps):\n"
                  << std::right << std::setw(8) << "row" << std::setw(10) << "1st entry" << std::setw(8) << "evt" << std::setw(10) << "nSteps"
                  << std::setw(9) << "nTracks" << std::setw(6) << "nPri" << std::setw(7) << "nPDG" << std::setw(12) << "sum edep"
                  << std::setw(12) << "max KE" << std::setw(12) << "max trkLen" << std::setw(10) << "in wcsimT" << std::endl;
        std::cout << std::string(104, '-') << std::endl;

        const std::set<int> &wcsimIds = seenIds;
        int nNotInWcsim = 0, nRepeatedRuns = 0;
        for (size_t i = 0; i < secRows.size(); ++i) {
            const SecRow &s = secRows[i];
            const bool inW = wcsimIds.count(s.evt) > 0;
            if (!inW) ++nNotInWcsim;
            if (runsPerEvt[s.evt] > 1) ++nRepeatedRuns;
            if (maxPrint >= 0 && (Long64_t)i >= maxPrint) {
                std::cout << "  ... (" << secRows.size() - maxPrint << " more rows not printed)" << std::endl;
                break;
            }
            std::cout << std::right << std::setw(8) << i << std::setw(10) << s.first << std::setw(8) << s.evt << std::setw(10) << s.nSteps
                      << std::setw(9) << s.tracks.size() << std::setw(6) << s.nPrimary << std::setw(7) << s.pdgs.size() << std::fixed
                      << std::setprecision(2) << std::setw(12) << s.edep << std::setw(12) << s.maxKE << std::setw(12) << s.maxTrkLen
                      << std::setw(10) << (inW ? "yes" : "NO") << std::endl;
        }

        std::cout << "\nAllSecondaries summary:\n"
                  << "  step entries            : " << nSec << "\n"
                  << "  event blocks (evt runs) : " << secRows.size() << "\n"
                  << "  distinct evt values     : " << runsPerEvt.size() << "\n"
                  << "  wcsimT entries          : " << nEntries << "\n"
                  << "  evt decreasing steps    : " << nBackwards << "\n"
                  << "  blocks w/ repeated evt  : " << nRepeatedRuns << "\n"
                  << "  blocks evt not in wcsimT: " << nNotInWcsim << std::endl;
        if (runsPerEvt.size() != secRows.size())
            std::cout << "  WARNING: same evt appears in several blocks -> evt IDs were probably not offset when merging files\n";
        if (secRows.size() != (size_t)nEntries)
            std::cout << "  WARNING: number of event blocks != wcsimT entries (events without secondaries are expected to be absent)\n";
    }

    file->Close();
    return status;
}

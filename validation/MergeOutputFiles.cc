#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#include "TFile.h"
#include "TTree.h"
#include "TBranch.h"
#include "TLeaf.h"

R__LOAD_LIBRARY($WCSIM_BUILD_DIR/lib/libWCSimRoot.so)

int MergeOutputFiles(const char *input_list, const char *output_filename)
{
    std::ifstream list(input_list);
    if (!list.is_open()) {
        std::cerr << "Error: cannot open input list: " << input_list << std::endl;
        return 1;
    }

    std::vector<std::string> input_files;
    std::string filename;
    while (std::getline(list, filename)) {
        if (!filename.empty()) input_files.push_back(filename);
    }
    if (input_files.empty()) {
        std::cerr << "Error: input list is empty" << std::endl;
        return 1;
    }

    TFile *output_file = TFile::Open(output_filename, "RECREATE");
    if (!output_file || output_file->IsZombie()) {
        std::cerr << "Error: cannot create output file: " << output_filename << std::endl;
        return 1;
    }

    // Event IDs restart at 0 in every input file, so shift them by the IDs already used.
    Int_t next_event_id = 0;
    TTree *event_out = nullptr;
    TTree *secondary_out = nullptr;
    std::vector<std::string> event_branch_names;
    std::vector<WCSimRootEvent *> events_buffer;
    Long64_t event_entries = 0;

    for (size_t file_index = 0; file_index < input_files.size(); ++file_index) {
        const std::string &input_file = input_files[file_index];
        TFile *file = TFile::Open(input_file.c_str(), "READ");
        if (!file || file->IsZombie()) {
            std::cerr << "Error: cannot open input ROOT file: " << input_file << std::endl;
            return 1;
        }

        TTree *events = nullptr;
        file->GetObject("wcsimT", events);
        if (!events) {
            std::cerr << "Error: missing wcsimT in " << input_file << std::endl;
            return 1;
        }

        if (file_index == 0) {
            for (int i = 0; i < events->GetListOfBranches()->GetEntries(); ++i) {
                TBranch *branch = (TBranch *)events->GetListOfBranches()->At(i);
                if (std::string(branch->GetClassName()) == "WCSimRootEvent") event_branch_names.push_back(branch->GetName());
            }
            if (event_branch_names.empty()) {
                std::cerr << "Error: no WCSimRootEvent branch in " << input_file << std::endl;
                return 1;
            }
            events_buffer.assign(event_branch_names.size(), nullptr);
            for (auto &buffer : events_buffer) buffer = new WCSimRootEvent();
        }
        for (size_t k = 0; k < event_branch_names.size(); ++k) events->SetBranchAddress(event_branch_names[k].c_str(), &events_buffer[k]);

        if (file_index == 0) {
            output_file->cd();
            event_out = events->CloneTree(0);
            if (!event_out) {
                std::cerr << "Error: failed to create output wcsimT" << std::endl;
                return 1;
            }
            for (const char *tree_name : {"wcsimGeoT", "wcsimRootOptionsT"}) {
                TTree *tree = nullptr;
                file->GetObject(tree_name, tree);
                if (!tree) {
                    std::cerr << "Error: missing " << tree_name << " in first input" << std::endl;
                    return 1;
                }
                output_file->cd();
                TTree *copy = tree->CloneTree(-1, "fast");
                if (!copy) {
                    std::cerr << "Error: failed to copy " << tree_name << std::endl;
                    return 1;
                }
                copy->Write(tree_name, TObject::kOverwrite);
            }
        }

        // Closing the previous input detached the output branches from the buffers.
        for (size_t k = 0; k < event_branch_names.size(); ++k) event_out->SetBranchAddress(event_branch_names[k].c_str(), &events_buffer[k]);

        const Int_t offset = next_event_id;
        Int_t max_new_id = offset - 1;
        for (Long64_t i = 0; i < events->GetEntries(); ++i) {
            if (events->GetEntry(i) <= 0) {
                std::cerr << "Error: failed to read wcsimT entry " << i << " of " << input_file << std::endl;
                return 1;
            }
            for (WCSimRootEvent *super_event : events_buffer) {
                for (int j = 0; j < super_event->GetNumberOfEvents(); ++j) {
                    WCSimRootEventHeader *header = super_event->GetTrigger(j)->GetHeader();
                    const Int_t new_id = header->GetEvtNum() + offset;
                    header->Set(new_id, header->GetRun(), header->GetDate(), header->GetSubEvtNumber());
                    if (new_id > max_new_id) max_new_id = new_id;
                }
            }
            event_out->Fill();
            ++event_entries;
        }

        TTree *secondaries = nullptr;
        file->GetObject("AllSecondaries", secondaries);
        if (secondaries) {
            if (!secondary_out) {
                output_file->cd();
                secondary_out = secondaries->CloneTree(0);
            }
            if (!secondary_out || !secondary_out->GetLeaf("evt")) {
                std::cerr << "Error: failed to set up AllSecondaries with an evt branch" << std::endl;
                return 1;
            }
            secondary_out->CopyAddresses(secondaries);
            Int_t *evt = (Int_t *)secondary_out->GetLeaf("evt")->GetValuePointer();
            for (Long64_t i = 0; i < secondaries->GetEntries(); ++i) {
                if (secondaries->GetEntry(i) <= 0) {
                    std::cerr << "Error: failed to read AllSecondaries entry " << i << " of " << input_file << std::endl;
                    return 1;
                }
                *evt += offset;
                if (*evt > max_new_id) max_new_id = *evt;
                secondary_out->Fill();
            }
            secondary_out->CopyAddresses(secondaries, true);
        }

        next_event_id = max_new_id + 1;
        file->Close();
        delete file;
    }

    output_file->cd();
    event_out->Write("wcsimT", TObject::kOverwrite);
    if (secondary_out) secondary_out->Write("AllSecondaries", TObject::kOverwrite);

    std::cout << "Merged " << event_entries << " wcsimT entries into " << output_filename << std::endl;
    output_file->Close();
    delete output_file;
    return 0;
}

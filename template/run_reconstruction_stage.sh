check_output $input_name $tree $origin $parent_input
if [[ -e $output || -e $marker ]]; then
    $python $helper --check-output $input $output $fingerprint $events || reconstruction_status=$$?
else
    bash $script || reconstruction_status=$$?
fi
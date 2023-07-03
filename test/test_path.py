from bssecop.SECoPSignal import Path




def test_path_nested_tuples():
    path = Path(module_name='mod',parameter_name='param')



    assert path.get_signal_name() == 'param'
    
    path = path.append(1)
    assert path.get_signal_name() == 'param-1'

    path = path.append(2)

    assert path.get_signal_name() == 'param-1-2'

    path = path.append(3)
    


    signame = path.get_signal_name()
    assert signame == 'param-1-2-3'
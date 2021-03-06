
import os
import math
import logging
logger = logging.getLogger('luigi-interface')
alloc_log = logging.getLogger('alloc_log')
alloc_log.setLevel(logging.DEBUG)

import luigi
from luigi.contrib.slurm import SlurmExecutableTask
from luigi.util import requires, inherits
from src.utils import CheckTargetNonEmpty

picard="java -XX:+UseSerialGC -Xmx{mem}M -jar /tgac/software/testing/picardtools/2.1.1/x86_64/bin/picard.jar"
python="source /usr/users/ga004/buntingd/FP_dev/dev/bin/activate"

# Ugly hack
script_dir = os.path.join(os.path.split(__file__)[0], 'scripts')

class ScatterVCF(SlurmExecutableTask):
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 8000
        self.n_cpu = 1
        self.partition = "tgac-medium"

    def work_script(self):
        return '''#!/bin/bash
                source vcftools-0.1.13;
                set -eo pipefail
                {python}
                mkdir -p {dir}/temp
                
                bgzip -cd {input} | python {script_dir}/spilt_VCF.py {dir}/temp/{base} {N_scatter}
                
                mv {dir}/temp/* {dir}
                rmdir {dir}/temp
                '''.format(python=python,
                           dir=os.path.split(self.output()[0].path)[0],
                           base=os.path.split(self.output()[0].path)[1][:-7],
                           script_dir=script_dir,
                           input=self.input().path,
                           N_scatter=len(self.output()))

class ScatterBED(luigi.Task, CheckTargetNonEmpty):

    def __init__(self, *args, **kwargs):
        super().__init__( *args, **kwargs)
    
    def run(self):
        with self.input().open() as fin:
            inp = [(l, int(l.split()[2]) - int(l.split()[1]) ) for l in fin]
            
        total_seq_len = sum([x[1] for x in inp])
        perfile = math.ceil(total_seq_len/len(self.output()))        
        
        inp_iter = iter(inp)
        files = 0
        for i,out in enumerate(self.output()):
            count = 0
            with out.open('w') as fout:
                for l,c in inp_iter:
                    fout.write(l)
                    count += c
                    if count > perfile:
                        break
                        
                files+=1                
                if files == len(self.output()):
                    #If we're on the final file dump the rest
                    fout.writelines([x[0] for x in inp_iter])

class GatherVCF(SlurmExecutableTask, CheckTargetNonEmpty):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs) 
        # Set the SLURM request params for this task
        self.mem = 8000
        self.n_cpu = 1
        self.partition = "tgac-medium"
        
    def work_script(self):
        return '''#!/bin/bash
                picard='{picard}'
                source vcftools-0.1.13;
                source jre-8u92
                
                set -eo pipefail
                $picard MergeVcfs O={output}.temp.vcf.gz {in_flags} 
                
                mv {output}.temp.vcf.gz {output}
                
                '''.format(picard=picard.format(mem=self.mem*self.n_cpu),
                           output=self.output().path,
                           in_flags=  "\\\n".join([" I= "+ x.path for x in self.input()])
                           )

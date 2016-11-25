import os,sys, json,shutil
import multiprocessing
from time import sleep
import time
import logging
logger = logging.getLogger('luigi-interface')
alloc_log = logging.getLogger('alloc_log')
alloc_log.setLevel(logging.DEBUG)

import luigi
from luigi.contrib.slurm import SlurmExecutableTask
from luigi.util import requires, inherits
from luigi import LocalTarget
from luigi.file import TemporaryFile
from src.utils import CheckTargetNonEmpty

picard="java -XX:+UseSerialGC -Xmx{mem}M -jar /tgac/software/testing/picardtools/2.1.1/x86_64/bin/picard.jar"
gatk="java -XX:+UseSerialGC -Xmx{mem}M -jar /tgac/software/testing/gatk/3.6.0/x86_64/bin/GenomeAnalysisTK.jar "
trimmomatic="java -XX:+UseSerialGC -Xmx{mem}M -jar /tgac/software/testing/trimmomatic/0.36/x86_64/bin/trimmomatic-0.36.jar "
python="source /usr/users/ga004/buntingd/FP_dev/dev/bin/activate"

# Ugly hack
script_dir = os.path.join(os.path.split(os.path.split(__file__)[0])[0], 'scripts')
log_dir = os.path.join(os.path.split(os.path.split(os.path.split(__file__)[0])[0])[0], 'logs')
os.makedirs(log_dir, exist_ok=True)

'''
Guidelines for harmonious living:
--------------------------------
1. Tasks acting on fastq files should output() a list like [_R1.fastq, _R2.fastq]
2. Tasks acting on a bam should just output() a single Target
3. Tasks acting on a vcf should just output() a single Target

Notes
-------
job.mem is actually mem_per_cpu
'''

class FetchFastqGZ(CheckTargetNonEmpty, SlurmExecutableTask):
    '''Fetches and concatenate the fastq.gz files for ``library`` from the /reads/ server
     :param str library: library name  '''
    
    library = luigi.Parameter()
    base_dir = luigi.Parameter(significant=False)
    scratch_dir = luigi.Parameter(default="/tgac/scratch/buntingd/", significant=False)
    read_dir = luigi.Parameter(default="/tgac/data/reads/*DianeSaunders*", significant=False)
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 1000
        self.n_cpu = 1
        self.partition = "tgac-medium"
        
    def complete(self):
        # Use a custom complete method to make sure the fastq files are not empty
        if not super().complete():
            logger.info("Failed to find fastq files for {0}".format(self.library))
            self.has_excessive_failures = lambda self : True
            self.disabled = True
            return False
        else:
            return True
        
    def output(self):
        LocalTarget(os.path.join(self.scratch_dir, self.library, "raw_R1.fastq.gz")).makedirs()
        return [LocalTarget(os.path.join(self.scratch_dir, self.library, "raw_R1.fastq.gz")),
                LocalTarget(os.path.join(self.scratch_dir, self.library, "raw_R2.fastq.gz"))]
    
    def work_script(self):
        return '''#!/bin/bash -e 
                  find {read_dir} -name "*{library}*_R1.fastq.gz" -type f  -exec cat {{}}   \; > {R1}.temp
                  find {read_dir} -name "*{library}*_R2.fastq.gz" -type f  -exec cat {{}}   \; > {R2}.temp
                  
                  mv {R1}.temp {R1}
                  mv {R2}.temp {R2}
                 '''.format(read_dir = self.read_dir,
                            library=self.library,
                            R1=self.output()[0].path,
                            R2=self.output()[1].path)  

@requires(FetchFastqGZ)
class Trimmomatic(CheckTargetNonEmpty, SlurmExecutableTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 2000
        self.n_cpu = 3
        self.partition = "tgac-medium"
    
    def output(self):
        return [LocalTarget(os.path.join(self.scratch_dir, self.library, "filtered_R1.fastq.gz")),
                LocalTarget(os.path.join(self.scratch_dir, self.library, "filtered_R2.fastq.gz"))]
    def work_script(self):
        return '''#!/bin/bash -e
               source jre-8u92
               source trimmomatic-0.30
               
               cd {scratch_dir}
               trimmomatic='{trimmomatic}'
               $trimmomatic PE -threads 8 {R1_in} {R2_in} -baseout temp.fastq.gz  ILLUMINACLIP:{adapters}:2:30:10:4 SLIDINGWINDOW:4:20 MINLEN:50
               
               #cat temp_1P.fastq.gz >> temp_1U.fastq.gz
               #rm temp_1P.fastq.gz
               #cat temp_2P.fastq.gz >> temp_2U.fastq.gz
               #rm temp_2P.fastq.gz
               
               mv temp_1P.fastq.gz {R1_out}
               mv temp_2P.fastq.gz {R2_out}
               
                '''.format(scratch_dir=os.path.join(self.scratch_dir, self.library),
                           trimmomatic=trimmomatic.format(mem=self.mem*self.n_cpu),
                           R1_in=self.input()[0].path,
                           R2_in=self.input()[1].path,
                           adapters='/tgac/software/testing/trimmomatic/0.30/x86_64/bin/adapters/TruSeq.cat.fa',
                           R1_out=self.output()[0].path,
                           R2_out=self.output()[1].path)

@requires(FetchFastqGZ)
class FastxQC(SlurmExecutableTask):
    '''Runs Fastx toolkit to plot the nucleotide and base call quality score distributions '''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 2000
        self.n_cpu = 1
        self.partition = "tgac-medium"
      
    def output(self):
        working_dir = os.path.join(self.base_dir, 'libraries', self.library)
        return {'stats_R1': LocalTarget(os.path.join(working_dir, 'QC', self.library + "_R1_stats.txt")),
                'stats_R2': LocalTarget(os.path.join(working_dir, 'QC', self.library + "_R2_stats.txt")),
                'boxplot_R1': LocalTarget(os.path.join(working_dir, 'QC', self.library + "_R1_quality.png")),
                'boxplot_R2': LocalTarget(os.path.join(working_dir, 'QC', self.library + "_R2_quality.png")),
                'nt_dist_R1': LocalTarget(os.path.join(working_dir, 'QC', self.library + "_R1_nt_distr.png")),
                'nt_dist_R2': LocalTarget(os.path.join(working_dir, 'QC', self.library + "_R2_nt_distr.png")),
            }
    
    def work_script(self):
        return '''#!/bin/bash -e
        source fastx_toolkit-0.0.13.2
        
        gzip -cd {R1_in} | fastx_quality_stats -o {stats_R1} -Q33
        gzip -cd {R2_in} | fastx_quality_stats -o {stats_R2} -Q33

        fastq_quality_boxplot_graph.sh -i {stats_R1} -o {boxplot_R1}
        fastq_quality_boxplot_graph.sh -i {stats_R2} -o {boxplot_R2}
                
        fastx_nucleotide_distribution_graph.sh -i {stats_R1} -o {nt_dist_R1}
        fastx_nucleotide_distribution_graph.sh -i {stats_R2} -o {nt_dist_R2}

        '''.format(R1_in=self.input()[0].path,
                   R2_in=self.input()[1].path,
                   stats_R1=self.output()['stats_R1'].path,
                   stats_R2=self.output()['stats_R2'].path,
                   boxplot_R1=self.output()['boxplot_R1'].path,
                   boxplot_R2=self.output()['boxplot_R2'].path,
                   nt_dist_R1=self.output()['nt_dist_R1'].path,
                   nt_dist_R2=self.output()['nt_dist_R2'].path)

@requires(FetchFastqGZ)
class FastxTrimmer(CheckTargetNonEmpty,SlurmExecutableTask):
    '''Uses FastxTrimmer to remove Illumina adaptors and barcodes'''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 1000
        self.n_cpu = 1
        self.partition = "tgac-medium"
    
    def output(self):
        return [LocalTarget(os.path.join(self.scratch_dir, self.library, "filtered_R1.fastq.gz")),
                LocalTarget(os.path.join(self.scratch_dir, self.library, "filtered_R2.fastq.gz"))]
    
    def work_script(self):
        return '''#!/bin/bash -e
        source fastx_toolkit-0.0.13.2
        
        gzip -cd {R1_in} | fastx_trimmer -f14 -Q33 | gzip > {R1_out}.temp ;
        gzip -cd {R2_in} | fastx_trimmer -f14 -Q33 | gzip > {R2_out}.temp ;
        
        mv {R1_out}.temp {R1_out}
        mv {R2_out}.temp {R2_out}
        '''.format(R1_in=self.input()[0].path,
                   R2_in=self.input()[1].path,
                   R1_out=self.output()[0].path,
                   R2_out=self.output()[1].path)

@requires(Trimmomatic)
class Star(CheckTargetNonEmpty, SlurmExecutableTask):
    '''Runs STAR to align to the reference :param str star_genome:'''
    star_genome = luigi.Parameter()
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 3000
        self.n_cpu = 4
        self.partition = "tgac-medium"
    
    def output(self):        
        return {
            'star_bam' : LocalTarget(os.path.join(self.scratch_dir, self.library, 'Aligned.sortedByCoord.out.bam')),
            'star_log' : LocalTarget(os.path.join(self.base_dir, 'libraries', self.library, 'Log.final.out'))
        }
    
    def work_script(self):
        return '''#!/bin/bash -e
                  source star-2.5.0a
                  mkdir -p {scratch_dir}/star_temp
                  cd  {scratch_dir}/star_temp
                  
                  STAR  --genomeDir {star_genome} --outSAMtype BAM SortedByCoordinate --runThreadN {n_cpu} --readFilesCommand gunzip -c --readFilesIn {R1} {R2}
                  
                  mv {scratch_dir}/star_temp/Log.final.out {star_log}
                  mv {scratch_dir}/star_temp/Aligned.sortedByCoord.out.bam {star_bam}
                  
                  '''.format(star_bam=self.output()['star_bam'].path,
                             star_log=self.output()['star_log'].path,
                             scratch_dir=os.path.join(self.scratch_dir, self.library),
                             star_genome=self.star_genome, 
                             n_cpu=self.n_cpu,
                             R1=self.input()[0].path,
                             R2=self.input()[1].path,)

@requires(Star)
class CleanSam(CheckTargetNonEmpty,SlurmExecutableTask):
    '''Cleans the provided SAM/BAM, soft-clipping beyond-end-of-reference alignments and setting MAPQ to 0 for unmapped reads'''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 1500
        self.n_cpu = 1
        self.partition = "tgac-short"
        
    def output(self):
        return LocalTarget(os.path.join(self.scratch_dir, self.library, 'Aligned.out_cleaned.bam'))
    
    def work_script(self):
        return '''#!/bin/bash -e
               source jre-8u92
               source picardtools-2.1.1
               picard='{picard}'
               $picard CleanSam VERBOSITY=ERROR QUIET=true I={input} O={output}.temp
               
               mv {output}.temp {output}
                '''.format(input=self.input()['star_bam'].path, 
                           output=self.output().path,
                           picard=picard.format(mem=self.mem*self.n_cpu))

@requires(CleanSam)
class AddReadGroups(CheckTargetNonEmpty,SlurmExecutableTask):
    '''Sets the read group to the sample name, required for GATK'''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 3000
        self.n_cpu = 1
        self.partition = "tgac-short"
        
    def output(self):
        return LocalTarget(os.path.join(self.scratch_dir, self.library, 'rg_added_sorted.bam'))
    
    def work_script(self):
        return '''#!/bin/bash -e
               source jre-8u92
               source picardtools-2.1.1
               picard='{picard}' 
               $picard AddOrReplaceReadGroups VERBOSITY=ERROR QUIET=true I={input} O={output}.temp SO=coordinate RGID=Star RGLB={lib} RGPL=Ilumina RGPU=Ilumina RGSM={lib}
               
               mv {output}.temp {output}                
                '''.format(input=self.input().path, 
                           output=self.output().path,
                           lib=self.library,
                           picard=picard.format(mem=self.mem*self.n_cpu))

@requires(AddReadGroups)
class MarkDuplicates(CheckTargetNonEmpty,SlurmExecutableTask):
    '''Marks optical/PCR duplicates'''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 6000
        self.n_cpu = 1
        self.partition = "tgac-short"
        
    def output(self):
        return LocalTarget(os.path.join(self.base_dir, 'libraries',self.library, 'dedupped.bam'))
    
    def work_script(self):
        return '''#!/bin/bash -e
               source jre-8u92
               source picardtools-2.1.1
               picard='{picard}'
               $picard MarkDuplicates VERBOSITY=ERROR QUIET=true I={input} O={output}.temp CREATE_INDEX=false VALIDATION_STRINGENCY=SILENT M=/dev/null
               
               mv {output}.temp {output}
                '''.format(input=self.input().path, 
                           output=self.output().path,
                           picard=picard.format(mem=self.mem*self.n_cpu))

@requires(MarkDuplicates)
class BaseQualityScoreRecalibration(SlurmExecutableTask):
    '''Runs BQSR. Because this requires a set of high quality SNPs to use
    as a ground truth we bootstrap this by first running the pipeline without
    BQSR then running again using the best SNPs of the first run.
    
    This is achieved by conditionally overriding run() on whether a snp_db is given 
    '''
    snp_db = luigi.Parameter(default='')
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 8000
        self.n_cpu = 1
        self.partition = "tgac-medium"
    
    def output(self):
        if self.snp_db == '':
            return LocalTarget(os.path.join(self.base_dir, 'libraries', self.library, 'dedupped.bam'))
        else:
            return LocalTarget(os.path.join(self.base_dir, 'libraries', self.library, 'recalibrated.bam'))
    
    def on_success(self):
        if self.snp_db == '':
            luigi.Task.on_success(self)
        else:
            SlurmExecutableTask.on_success(self)

    def on_failure(self,e):
        if self.snp_db == '':
            luigi.Task.on_failure(self, e)
        else:
            SlurmExecutableTask.on_failure(self,e)
            
    def run(self):
        if self.snp_db == '':
            logger.info("Not running BQSR as no snp_db given")
            
        else:
            logger.info("Running BQSR recalibration using bootstrapped snp_db " +  self.snp_db)
            super().run()
    
    def work_script(self):
        recal = os.path.join(self.base_dir, 'libraries', self.library, self.library+"_recal.tsv")
        return '''#!/bin/bash -e
                  source jre-8u92
                  source gatk-3.6.0
                  gatk='{gatk}'
                  $gatk -T BaseRecalibrator  -R {reference}  -I {input}  -knownSites {snp_db}  -o {recal}
                  $gatk -T PrintReads -R {reference} -I {input} -BQSR {recal} -o {output}.temp
                  
                  mv {output}.temp {output}
                '''.format(gatk=gatk.format(mem=self.mem*self.n_cpu),
                           input=self.input().path,
                           output=self.output().path,
                           reference=self.reference,
                           recal=recal)

@requires(BaseQualityScoreRecalibration)
class SplitNCigarReads(CheckTargetNonEmpty,SlurmExecutableTask):
    '''Required by GATK, breaks up reads spanning introns'''
    reference = luigi.Parameter()
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 6000
        self.n_cpu = 1
        self.partition = "tgac-short"
        
    def output(self):
        return LocalTarget(os.path.join(self.scratch_dir, self.library, 'split.bam'))
    
    def work_script(self):
        return '''#!/bin/bash -e
               source jre-8u92
               source gatk-3.6.0
               gatk='{gatk}'
               picard='{picard}'
               
               $picard BuildBamIndex VERBOSITY=ERROR QUIET=true I={input}
               $gatk -T SplitNCigarReads --logging_level ERROR -R {reference} -I {input} -o {output}.temp -rf ReassignOneMappingQuality -RMQF 255 -RMQT 60 -U ALLOW_N_CIGAR_READS
               
               mv {output}.temp.bai {output}.bai
               mv {output}.temp {output}
                '''.format(input=self.input().path, 
                           output=self.output().path,
                           picard=picard.format(mem=self.mem*self.n_cpu),
                           gatk=gatk.format(mem=self.mem*self.n_cpu),
                           reference=self.reference) 

@requires(SplitNCigarReads)
class HaplotypeCaller(CheckTargetNonEmpty, SlurmExecutableTask):
    '''Per sample SNP calling'''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 6000
        self.n_cpu = 1
        self.partition = "tgac-medium"
        
    def output(self):
        return LocalTarget(os.path.join(self.base_dir, 'libraries', self.library, self.library + ".g.vcf"))
        
    def work_script(self):
        return '''#!/bin/bash -e
                source jre-8u92
                source gatk-3.6.0
                gatk='{gatk}'
                
                $gatk -T HaplotypeCaller  -R {reference} -I {input} -dontUseSoftClippedBases --variant_index_type LINEAR --variant_index_parameter 128000 --emitRefConfidence GVCF -o {output}.temp
                
                mv {output}.temp {output}
        '''.format(input=self.input().path, 
                   output=self.output().path,
                   gatk=gatk.format(mem=self.mem*self.n_cpu),
                   reference=self.reference) 

@requires(HaplotypeCaller)
class PlotAlleleFreq(SlurmExecutableTask):
    '''Make plots of the ranked allele frequencies to identify mixed isolates'''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 4000
        self.n_cpu = 1
        self.partition = "tgac-medium"
        
    def output(self):
        return LocalTarget(os.path.join(self.base_dir, 'libraries', self.library, 'QC', self.library + "_allele_freqs.pdf"))
        
    def work_script(self):
        self.temp1=TemporaryFile()
        self.temp2=TemporaryFile()
        return '''#!/bin/bash -e
                source jre-8u92
                {python}
                source gatk-3.6.0
                gatk='{gatk}'
                
                $gatk -T VariantsToTable -R {reference} -AMD -V {input} -F CHROM -F POS -F REF -F ALT -F DP -GF AD  --out {temp1}
                grep -ve "NA" <  {temp1}  > {temp2}

                python {script_dir}/plotAF.py {temp2} {output}
                '''.format(python=python,
                            script_dir=script_dir,
                            gatk=gatk.format(mem=self.mem*self.n_cpu),
                            reference=self.reference,
                            input=self.input().path,
                            output=self.output().path,
                            temp1=self.temp1.path,
                            temp2=self.temp2.path)

@inherits(SplitNCigarReads)
@inherits(FastxQC)
@inherits(PlotAlleleFreq)
class PerLibPipeline(luigi.WrapperTask):
    '''Wrapper task that runs all tasks on a single library'''
    def requires(self):
        yield self.clone(FastxQC)
        yield self.clone(HaplotypeCaller)
        yield self.clone(PlotAlleleFreq)

@requires(PerLibPipeline)
class CleanUpLib(luigi.Task):
    priority = 100
    def run(self):
        shutil.rmtree(os.path.join(self.scratch_dir, self.library), ignore_errors=True)
    def complete(self):
        return self.clone_parent().complete() and not os.path.exists(os.path.join(self.scratch_dir, self.library))

@inherits(CleanUpLib)        
class LibraryBatchWrapper(luigi.WrapperTask):
    '''Wrapper task to execute the per library part of the pipline on all
        libraries in :param list lib_list:'''
    lib_list = luigi.ListParameter()        
    library=None
    def requires(self):
        for lib in self.lib_list:
            yield self.clone_parent(library=lib.rstrip())
# This is a bit of a hack, it allows us to pass parameters to LibraryBatchWrapper and have them propagate
# down to all calls to PerLibPipeline.
LibraryBatchWrapper.library=None


#-----------------------------------------------------------------------#


if __name__ == '__main__':
    os.environ['TMPDIR'] = "/tgac/scratch/buntingd"
    logging.disable(logging.DEBUG)
    timestr = time.strftime("%Y%m%d-%H%M%S")
    
    fh = logging.FileHandler(os.path.join(log_dir, os.path.basename(__file__) + "_" + timestr + ".log"))
    fh.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    
    alloc_fh = logging.FileHandler(os.path.join(log_dir, os.path.basename(__file__) + "_" + timestr + ".salloc.log"))
    alloc_fh.setLevel(logging.INFO)
    formatter = logging.Formatter('%(message)s')
    alloc_fh.setFormatter(formatter)
    alloc_log.addHandler(alloc_fh)
    
    with open(sys.argv[1], 'r') as libs_file:
        lib_list = [line.rstrip() for line in libs_file]
    
    luigi.run(['LibraryBatchWrapper', '--lib-list', json.dumps(lib_list),
                             '--star-genome', '/tgac/workarea/collaborators/saunderslab/Realignment/data/genome/',
                             '--reference', '/tgac/workarea/collaborators/saunderslab/Realignment/data/PST130_contigs.fasta'] + sys.argv[2:])